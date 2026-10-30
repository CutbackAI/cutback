"""Prepare, fit and independently evaluate native input-size routing policies."""

import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException, Query
from pydantic import UUID4, Field
from sqlalchemy import select

from cutback.core.input_routing import LearnedRoutingBinding, RoutingTrainingExecution
from cutback.core.models import StrictModel
from cutback.evals.datasets import digest
from cutback.evals.experiments import Budget, CriterionSpec, ExperimentPlan, ModelSpec
from cutback.evals.learned_routing import RoutingFitOptions, fit_routes
from cutback.evals.metrics import MetricSpec
from cutback.server.campaign_content import content_transaction
from cutback.server.content_policy import policy_content
from cutback.server.dataset_lineage import lock_lineage, lock_preparation_identity
from cutback.server.db import AuditEvent, CapturePolicyRecord, DatasetRecord, PayloadSample
from cutback.server.evaluation_api import ContentPurpose, PlanInput
from cutback.server.routing_service import authenticated_training, read_fit

FIT_REFUSALS = {
    "ROUTING_FIT_COMPUTE_BOUND",
    "ROUTING_PRIVATE_EVIDENCE_READ_BOUND",
    "ROUTING_TRAINING_NO_FEASIBLE_PARTITION",
    "ROUTING_TRAINING_NO_LOWER_COST_FEASIBLE_POLICY",
    "ROUTING_TRAINING_GROUPS_INSUFFICIENT",
    "ROUTING_TRAINING_BASELINE_COST_UNKNOWN",
    "ROUTING_TRAINING_OUTSIDE_BASELINE_INPUT_BOUND",
    "ROUTING_TRAINING_PAIRED_RECEIPTS_INCOMPLETE",
    "ROUTING_TRAINING_CANONICAL_CASE_RECEIPT_REQUIRED",
    "ROUTING_TRAINING_CALL_RECEIPT_CHANGED_OR_UNCERTAIN",
    "ROUTING_TRAINING_COST_RECEIPT_MISMATCH",
    "ROUTING_TRAINING_RETAINED_SEMANTICS_CHANGED",
    "ROUTING_SHARED_CONTENT_QUOTA_EXHAUSTED",
    "ROUTING_TRAINING_FAMILY_OVERLAPS_KNOWN_TEST_ASSIGNMENT",
    "CONTENT_EXCEEDS_POLICY_BYTES",
    "ARTIFACT_REQUIRES_APPLICATION_DEIDENTIFICATION",
    "PAYLOAD_STORAGE_DISABLED",
}


class RoutingTrainingDraftInput(StrictModel):
    request_id: UUID4
    content_nonce: UUID4
    dataset_id: UUID
    baseline: ModelSpec
    candidates: list[ModelSpec] = Field(min_length=1, max_length=4)
    metric: MetricSpec
    criteria: list[CriterionSpec] = Field(default_factory=list, max_length=20)
    budget: Budget
    max_cases: int = Field(default=256, strict=True, ge=2, le=256)
    transport: Literal["native_provider", "scripted_test_only"] = "native_provider"


class RoutingFitInput(ContentPurpose):
    request_id: UUID4
    training_run_id: UUID
    options: RoutingFitOptions = Field(default_factory=RoutingFitOptions)


class RoutingValidationDraftInput(ContentPurpose):
    request_id: UUID4
    content_nonce: UUID4
    budget: Budget


class RoutingConfirmationDraftInput(ContentPurpose):
    request_id: UUID4
    validation_run_id: UUID
    budget: Budget


def summary(sample):
    return {
        "id": str(sample.id),
        "version_sha256": sample.content_hash,
        "training_run_id": sample.metadata_safe["training_run_id"],
        "dataset_id": sample.metadata_safe["dataset_id"],
        "training_cases": sample.metadata_safe["training_cases"],
        "training_groups": sample.metadata_safe["training_groups"],
        "expires_at": sample.expires_at,
        "heldout_quality_claim": False,
        "requires_validation_and_untouched_confirmation": True,
    }


def install_routing(app, sessions, authorize, scope, create_plan):
    from cutback.server.evaluation_api import policy_object_count, require_store

    authorized = Depends(authorize)

    @app.post("/v1/projects/{project_id}/routing/training-drafts", status_code=201)
    def training_draft(project_id: UUID, body: RoutingTrainingDraftInput, auth=authorized):
        scope(auth, project_id, ("admin",))
        with sessions() as session:
            dataset = session.get(DatasetRecord, body.dataset_id)
            if not dataset or dataset.project_id != project_id:
                raise HTTPException(404, "DATASET_NOT_FOUND")
            try:
                providers: list[Literal["openai", "anthropic", "local"]] = []
                if any(model.provider == "openai" for model in [body.baseline, *body.candidates]):
                    providers.append("openai")
                if any(
                    model.provider == "anthropic" for model in [body.baseline, *body.candidates]
                ):
                    providers.append("anthropic")
                plan = ExperimentPlan(
                    name="routing-training-" + body.request_id.hex[:12],
                    workflow=dataset.workflow_id,
                    dataset_hash=dataset.immutable_hash,
                    baseline=body.baseline,
                    candidates=body.candidates,
                    metric=body.metric,
                    criteria=body.criteria,
                    budget=body.budget,
                    approved_providers=providers,
                    split="train",
                    evaluator_version="deterministic-v15",
                    content_nonce=body.content_nonce,
                    retain_case_outputs=True,
                    routing_execution=RoutingTrainingExecution(
                        max_cases=body.max_cases, transport=body.transport
                    ),
                )
            except ValueError:
                raise HTTPException(
                    422, "ROUTING_REQUIRES_BOUNDED_NATIVE_TRAINING_SPECIFICATION"
                ) from None
        return create_plan(
            project_id,
            PlanInput(request_id=body.request_id, dataset_id=body.dataset_id, specification=plan),
            auth,
        )

    @app.get("/v1/projects/{project_id}/routing/fits")
    def list_fits(
        project_id: UUID,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
        auth=authorized,
    ):
        scope(auth, project_id)
        with sessions() as session:
            rows = session.scalars(
                select(PayloadSample)
                .where(
                    PayloadSample.project_id == project_id,
                    PayloadSample.metadata_safe["kind"].as_string() == "learned_routing_fit",
                    PayloadSample.expires_at > datetime.now(UTC),
                )
                .order_by(PayloadSample.id)
                .limit(limit)
                .offset(offset)
            ).all()
            return [summary(row) for row in rows if not row.metadata_safe.get("deleted")]

    @app.get("/v1/projects/{project_id}/routing/training-runs")
    def training_runs(
        project_id: UUID,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
        auth=authorized,
    ):
        from cutback.server.db import ExperimentPlanRecord, ExperimentRunRecord

        scope(auth, project_id)
        with sessions() as session:
            rows = session.execute(
                select(ExperimentPlanRecord, ExperimentRunRecord)
                .outerjoin(
                    ExperimentRunRecord, ExperimentRunRecord.plan_id == ExperimentPlanRecord.id
                )
                .where(
                    ExperimentPlanRecord.project_id == project_id,
                    ExperimentPlanRecord.specification["public_specification"]["routing_execution"][
                        "phase"
                    ].as_string()
                    == "training",
                )
                .order_by(ExperimentPlanRecord.id)
                .limit(limit)
                .offset(offset)
            ).all()
            return [
                {
                    "id": str(plan.id),
                    "dataset_id": str(plan.dataset_id),
                    "plan_hash": plan.specification["plan_hash"],
                    "specification": plan.specification["public_specification"],
                    "approval_status": plan.approval_status,
                    "status": run.status if run else None,
                    "run_id": str(run.id) if run else None,
                }
                for plan, run in rows
            ]

    @app.post("/v1/projects/{project_id}/routing/fits", status_code=201)
    def fit(project_id: UUID, body: RoutingFitInput, auth=authorized):
        scope(auth, project_id, ("admin",))
        store = require_store(app)
        request_hash = digest(body.model_dump(mode="json"))
        with content_transaction(sessions, store, project_id) as (session, refs):
            lock_lineage(session, project_id)
            lock_preparation_identity(session, "learned-routing-fit", body.request_id)
            prior = session.get(PayloadSample, body.request_id, populate_existing=True)
            if prior:
                if (
                    prior.project_id != project_id
                    or prior.metadata_safe.get("kind") != "learned_routing_fit"
                    or prior.metadata_safe.get("request_hash") != request_hash
                    or prior.metadata_safe.get("created_by") != auth["actor"]
                ):
                    raise HTTPException(409, "ROUTING_FIT_REQUEST_ID_UNAVAILABLE_OR_INTENT_CHANGED")
                try:
                    read_fit(session, store, project_id, prior.id)
                except ValueError:
                    raise HTTPException(
                        409, "ROUTING_FIT_CURRENT_SOURCE_OR_CONTENT_UNAVAILABLE"
                    ) from None
                return summary(prior)
            started = time.perf_counter()
            try:
                evidence, training, dataset, policy_id, policy, expiry = authenticated_training(
                    session, store, project_id, body.training_run_id
                )
                result = fit_routes(evidence, body.options)
                # Fitting has no vendor call; unknown host/compute costs remain
                # unknown instead of claiming that this operation was free.
                value = {
                    "version": "cutback.learned-routing-artifact.v1",
                    "content_nonce": str(uuid4()),
                    "training_run_id": str(body.training_run_id),
                    "dataset_id": str(dataset.id),
                    "training_plan_sha256": evidence.source_plan_sha256,
                    "training_dataset_sha256": evidence.dataset_sha256,
                    "fit": result,
                    "fit_elapsed_ms": (time.perf_counter() - started) * 1000,
                    "fit_compute_usd": None,
                    "fit_provider_requests": 0,
                    "training_setup_known_cost_usd": str(
                        sum(
                            (
                                row.known_cost_usd
                                for row in evidence.observations
                                if row.known_cost_usd is not None
                            ),
                            start=Decimal(0),
                        )
                    ),
                    "training_evidence": evidence.model_dump(mode="json"),
                }
                policy_row = session.get(CapturePolicyRecord, policy_id, with_for_update=True)
                if (
                    not policy_row
                    or not policy_row.enabled
                    or policy_object_count(session, policy_id) >= policy.max_samples
                ):
                    raise ValueError("ROUTING_SHARED_CONTENT_QUOTA_EXHAUSTED")
                clean, blob = policy_content(value, policy, preserve_semantics=True)
                ref = store.put(project_id, blob, policy.retention_days)
                refs.append(ref)
            except ValueError as exc:
                code = str(exc)
                raise HTTPException(
                    409,
                    code
                    if code in FIT_REFUSALS
                    else "ROUTING_REQUIRES_COMPLETE_CURRENT_FEASIBLE_TRAINING_EVIDENCE",
                ) from None
            sample = PayloadSample(
                id=body.request_id,
                project_id=project_id,
                policy_id=policy_id,
                object_ref=ref,
                content_hash=digest(clean),
                expires_at=min(expiry, datetime.now(UTC) + timedelta(days=policy.retention_days)),
                metadata_safe={
                    "kind": "learned_routing_fit",
                    "request_hash": request_hash,
                    "created_by": auth["actor"],
                    "training_run_id": str(body.training_run_id),
                    "dataset_id": str(dataset.id),
                    "training_cases": result["training_cases"],
                    "training_groups": result["training_groups"],
                },
            )
            session.add(sample)
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="routing_policy_fitted",
                    details={
                        "fit_id": str(sample.id),
                        "training_run_id": str(body.training_run_id),
                        "purpose": body.purpose,
                        "provider_requests": 0,
                    },
                )
            )
            session.flush()
            return summary(sample)

    @app.post("/v1/projects/{project_id}/routing/fits/{fit_id}/content")
    def content(project_id: UUID, fit_id: UUID, body: ContentPurpose, auth=authorized):
        scope(auth, project_id, ("admin",))
        store = require_store(app)
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            try:
                sample, value, _, _ = read_fit(session, store, project_id, fit_id)
            except ValueError:
                raise HTTPException(404, "CURRENT_ROUTING_FIT_NOT_AVAILABLE") from None
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="routing_fit_content_read",
                    details={"fit_id": str(fit_id), "purpose": body.purpose},
                )
            )
            return {"version_sha256": sample.content_hash, "content": value}

    @app.post("/v1/projects/{project_id}/routing/fits/{fit_id}/validation-drafts", status_code=201)
    def validation_draft(
        project_id: UUID, fit_id: UUID, body: RoutingValidationDraftInput, auth=authorized
    ):
        scope(auth, project_id, ("admin",))
        store = require_store(app)
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            try:
                sample, value, training, dataset = read_fit(session, store, project_id, fit_id)
                plan = ExperimentPlan.model_validate(
                    {
                        **training.model_dump(mode="json"),
                        "name": "routing-validation-" + fit_id.hex[:12],
                        "split": "validation",
                        "candidates": [value["fit"]["candidate"]],
                        "budget": body.budget.model_dump(mode="json"),
                        "content_nonce": str(body.content_nonce),
                        "routing_execution": LearnedRoutingBinding(
                            fit_id=fit_id,
                            fit_sha256=sample.content_hash,
                            training_run_id=UUID(value["training_run_id"]),
                            transport=training.routing_execution.transport,
                        ).model_dump(mode="json"),
                    }
                )
            except ValueError:
                raise HTTPException(
                    409, "ROUTING_FIT_CURRENT_SOURCE_OR_CONTENT_UNAVAILABLE"
                ) from None
            dataset_id = dataset.id
        return create_plan(
            project_id,
            PlanInput(request_id=body.request_id, dataset_id=dataset_id, specification=plan),
            auth,
        )

    @app.post(
        "/v1/projects/{project_id}/routing/fits/{fit_id}/confirmation-drafts", status_code=201
    )
    def confirmation_draft(
        project_id: UUID, fit_id: UUID, body: RoutingConfirmationDraftInput, auth=authorized
    ):
        from cutback.server.db import ExperimentPlanRecord, ExperimentRunRecord
        from cutback.server.plan_artifacts import load_plan

        scope(auth, project_id, ("admin",))
        store = require_store(app)
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            try:
                sample, _, _, dataset = read_fit(session, store, project_id, fit_id)
                run = session.get(ExperimentRunRecord, body.validation_run_id)
                if not run or run.project_id != project_id or run.status != "COMPLETED":
                    raise ValueError
                row = session.get(ExperimentPlanRecord, run.plan_id)
                prior = load_plan(store, project_id, row.specification)
                if (
                    prior.split != "validation"
                    or not isinstance(prior.routing_execution, LearnedRoutingBinding)
                    or prior.routing_execution.fit_id != fit_id
                    or prior.routing_execution.fit_sha256 != sample.content_hash
                ):
                    raise ValueError
                plan = ExperimentPlan.model_validate(
                    {
                        **prior.model_dump(mode="json"),
                        "name": "routing-confirmation-" + fit_id.hex[:12],
                        "split": "test",
                        "predecessor_run_id": str(run.id),
                        "budget": body.budget.model_dump(mode="json"),
                    }
                )
            except ValueError:
                raise HTTPException(409, "ROUTING_REQUIRES_COMPLETED_MATCHING_VALIDATION") from None
            dataset_id = dataset.id
        # The existing stage authority checks quality, one selected candidate,
        # exact contracts and fresh held-out families before this can execute.
        return create_plan(
            project_id,
            PlanInput(request_id=body.request_id, dataset_id=dataset_id, specification=plan),
            auth,
        )
