"""One approved criteria-generation call, explicit owner review, then pinned checks."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException, Query
from pydantic import UUID4, Field
from sqlalchemy import func, select

from cutback.core.evaluation_criteria import ReviewedCriteriaBinding
from cutback.core.models import Identifier, StrictModel
from cutback.core.request_profiles import SourceBinding
from cutback.evals.criteria_generation import (
    CriteriaGenerationPlan,
    CriteriaRequirements,
    generation_input,
    generator_model,
)
from cutback.evals.experiments import Budget, ExperimentPlan, ModelSpec, upper_cost
from cutback.server.criteria_db import CriteriaVersionRecord
from cutback.server.db import (
    AuditEvent,
    DatasetItem,
    DatasetRecord,
    Environment,
    ExperimentPlanRecord,
    ExperimentRunRecord,
    JobRecord,
    PayloadSample,
)


class CriteriaDraftInput(StrictModel):
    request_id: UUID4 | None = None
    dataset_id: UUID
    requirements: CriteriaRequirements
    generator: ModelSpec
    budget: Budget
    max_training_examples: int = Field(default=4, strict=True, ge=1, le=8)
    transport: Literal["native_provider", "scripted_test_only"] = "native_provider"


class CriteriaBudgetApproval(StrictModel):
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    approval_ref: Identifier


class CriteriaQueueInput(StrictModel):
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class CriteriaReviewInput(StrictModel):
    version_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["approve", "reject"]
    approval_ref: Identifier
    expected_review_approval_id: UUID | None = None


class CriteriaCancelInput(StrictModel):
    reason: Identifier


class CriteriaEvaluationInput(StrictModel):
    request_id: UUID4 | None = None
    content_nonce: UUID4 | None = None
    dataset_id: UUID
    criteria_binding: ReviewedCriteriaBinding
    baseline: ModelSpec
    candidates: list[ModelSpec] = Field(min_length=1, max_length=5)
    budget: Budget
    repeats: int = Field(default=1, strict=True, ge=1, le=5)
    retain_case_outputs: bool = False
    source_binding: SourceBinding | None = None
    name: Identifier = "reviewed-criteria-validation"


class CriteriaConfirmationInput(StrictModel):
    validation_run_id: UUID
    candidate_key: Identifier
    budget: Budget


def install_criteria(app, sessions, authorize, scope, create_plan):
    from cutback.server.campaign_content import content_transaction
    from cutback.server.criteria_content import put_payload, training_view
    from cutback.server.criteria_service import prepared_generation
    from cutback.server.dataset_lineage import expose, lock_lineage
    from cutback.server.evaluation_api import active_policy, policy_object_count, require_store
    from cutback.server.plan_artifacts import freeze_plan
    from cutback.server.principals import principal_role

    authorized = Depends(authorize)

    def found(session, project, identifier, *, lock=False):
        row = session.get(
            CriteriaVersionRecord, identifier, with_for_update=lock, populate_existing=True
        )
        if not row or row.project_id != project:
            raise HTTPException(404, "CRITERIA_VERSION_NOT_FOUND")
        return row

    def public(session, row):
        plan = session.get(ExperimentPlanRecord, row.generation_plan_id)
        run = (
            session.get(ExperimentRunRecord, row.generation_run_id)
            if row.generation_run_id
            else None
        )
        return {
            "id": str(row.id),
            "dataset_id": str(row.dataset_id),
            "state": run.status if run and run.status in {"FAILED", "CANCELLED"} else row.state,
            "review_status": row.review_status,
            "generation_plan_id": str(row.generation_plan_id),
            "plan_hash": row.generation_plan_sha256,
            "generation_approval_status": plan.approval_status,
            "generation_run_id": str(row.generation_run_id) if row.generation_run_id else None,
            "version_sha256": row.version_sha256,
            "review_approval_id": str(row.review_approval_id) if row.review_approval_id else None,
            "created_at": row.created_at,
            "expires_at": row.expires_at,
            "request_reservations": run.requests_used if run else 0,
            "known_cost_requests": run.known_cost_requests if run else 0,
            "billing_complete": (
                None
                if run and run.known_cost_requests is None
                else run.requests_used == run.known_cost_requests
                if run
                else True
            ),
            "known_spend_subtotal_usd": str(run.spent_usd) if run else "0",
            "proposed_criteria_count": run.results.get("proposed_criteria_count") if run else None,
            "code": run.results.get("code") if run else None,
            "quality_evidence": "none_proposal_and_owner_review_only",
        }

    @app.post("/v1/projects/{project_id}/criteria/drafts", status_code=201)
    def draft(project_id: UUID, body: CriteriaDraftInput, auth=authorized):
        scope(auth, project_id, ("analyst",))
        store = require_store(app)
        with content_transaction(sessions, store, project_id) as (session, refs):
            lock_lineage(session, project_id)
            if body.request_id:
                from cutback.server.dataset_lineage import lock_preparation_identity

                lock_preparation_identity(session, "criteria-draft", body.request_id)
                previous = session.get(CriteriaVersionRecord, body.request_id, with_for_update=True)
                if previous:
                    if previous.project_id != project_id:
                        raise HTTPException(409, "CRITERIA_REQUEST_ID_UNAVAILABLE")
                    try:
                        frozen, policy, value, requirements, _ = prepared_generation(
                            session, store, previous
                        )
                        source = session.get(DatasetRecord, body.dataset_id)
                        if (
                            not source
                            or source.project_id != project_id
                            or previous.dataset_id != body.dataset_id
                        ):
                            raise ValueError
                        training, identities = training_view(
                            session,
                            store,
                            project_id,
                            source,
                            policy,
                            limit=body.max_training_examples,
                        )
                        if (
                            requirements != body.requirements
                            or frozen.baseline != generator_model(body.generator)
                            or frozen.budget != body.budget
                            or frozen.transport != body.transport
                            or training != value["training"]
                            or identities != value["source_item_ids"]
                        ):
                            raise ValueError
                    except ValueError:
                        raise HTTPException(
                            409, "CRITERIA_REQUEST_ID_CONTENT_OR_RIGHTS_CHANGED"
                        ) from None
                    return {
                        **public(session, previous),
                        "existing_draft": True,
                        "budget": frozen.budget.model_dump(mode="json"),
                        "worst_case_modeled_usd": str(upper_cost(frozen.baseline)),
                        "fits_budget": True,
                        "provider_calls_made_in_http": 0,
                    }
            dataset = session.get(DatasetRecord, body.dataset_id)
            if not dataset or dataset.project_id != project_id:
                raise HTTPException(404, "DATASET_NOT_FOUND")
            _, policy = active_policy(session, project_id, UUID(dataset.provenance["policy_id"]))
            if (
                policy_object_count(session, UUID(dataset.provenance["policy_id"])) + 2
                > policy.max_samples
            ):
                raise HTTPException(429, "CRITERIA_INPUT_AND_OUTPUT_RESERVATION_QUOTA")
            if "openai" not in policy.allowed_providers:
                raise HTTPException(403, "CRITERIA_GENERATOR_VENDOR_NOT_AUTHORIZED")
            environment = session.scalar(
                select(Environment).where(
                    Environment.project_id == project_id,
                    Environment.name == dataset.provenance["environment"],
                )
            )
            if not environment:
                raise HTTPException(409, "CRITERIA_ENVIRONMENT_NOT_REGISTERED")
            for model in (CriteriaVersionRecord, ExperimentPlanRecord):
                if (
                    session.scalar(
                        select(func.count())
                        .select_from(model)
                        .where(model.project_id == project_id)
                    )
                    >= 1000
                ):
                    raise HTTPException(429, "CRITERIA_PROJECT_PLAN_OR_VERSION_QUOTA")
            try:
                training, source_ids = training_view(
                    session, store, project_id, dataset, policy, limit=body.max_training_examples
                )
                model = generator_model(body.generator)
                text = generation_input(body.requirements, training)
                if (
                    len(text.encode()) + len(model.instruction.encode()) + 4096
                    > model.max_input_tokens
                    or body.budget.max_requests != 1
                    or upper_cost(model) > body.budget.max_usd
                ):
                    raise ValueError("CRITERIA_ONE_CALL_INPUT_OR_BUDGET_BOUND")
                expiry = session.scalar(
                    select(func.min(PayloadSample.expires_at))
                    .join(DatasetItem, DatasetItem.payload_id == PayloadSample.id)
                    .where(DatasetItem.id.in_([UUID(value) for value in source_ids]))
                )
                expires = min(
                    policy.expires_at,
                    expiry,
                    datetime.now(UTC) + timedelta(days=policy.retention_days),
                )
                row = CriteriaVersionRecord(
                    id=body.request_id or uuid4(),
                    project_id=project_id,
                    dataset_id=dataset.id,
                    environment_id=environment.id,
                    capture_policy_id=UUID(dataset.provenance["policy_id"]),
                    source_dataset_sha256=dataset.immutable_hash,
                    created_by=auth["actor"],
                    expires_at=expires,
                    state="PENDING",
                    review_status="PENDING",
                )
                payload = put_payload(
                    session,
                    store,
                    row,
                    {
                        "input": text,
                        "requirements": body.requirements.model_dump(mode="json"),
                        "training": training,
                        "source_item_ids": source_ids,
                    },
                    "criteria_input",
                    refs,
                    policy,
                )
                row.input_payload_id = payload.id
                plan = CriteriaGenerationPlan(
                    criteria_id=row.id,
                    transport=body.transport,
                    workflow=dataset.workflow_id,
                    dataset_hash=dataset.immutable_hash,
                    input_payload_id=payload.id,
                    input_sha256=payload.content_hash,
                    baseline=model,
                    budget=body.budget,
                )
                metadata = freeze_plan(store, project_id, plan, policy, row.capture_policy_id)
                metadata["expires_at"] = expires.isoformat()
                refs.append(metadata["artifact_ref"])
                planned = ExperimentPlanRecord(
                    id=uuid4(), project_id=project_id, dataset_id=dataset.id, specification=metadata
                )
                session.add(planned)
                session.flush()
                row.generation_plan_id = planned.id
                row.generation_plan_sha256 = metadata["plan_hash"]
                session.add(row)
                expose(
                    session, project_id, dataset.id, "train", "criteria-preparation:" + str(row.id)
                )
                session.add(
                    AuditEvent(
                        project_id=project_id,
                        actor=auth["actor"],
                        action="criteria_generation_planned",
                        details={
                            "criteria_id": str(row.id),
                            "training_rows": len(training),
                            "plan_hash": metadata["plan_hash"],
                        },
                    )
                )
                session.flush()
            except ValueError:
                raise HTTPException(
                    422, "CRITERIA_TRAINING_GENERATOR_OR_BUDGET_NOT_QUALIFIED"
                ) from None
            return {
                **public(session, row),
                "budget": body.budget.model_dump(mode="json"),
                "generator_provider": model.provider,
                "generator_model": model.model,
                "worst_case_modeled_usd": str(upper_cost(model)),
                "fits_budget": True,
                "provider_calls": 0,
            }

    @app.get("/v1/projects/{project_id}/criteria")
    def list_versions(
        project_id: UUID,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
        auth=authorized,
    ):
        scope(auth, project_id, ("viewer",))
        with sessions() as session:
            rows = session.scalars(
                select(CriteriaVersionRecord)
                .where(CriteriaVersionRecord.project_id == project_id)
                .order_by(CriteriaVersionRecord.created_at.desc(), CriteriaVersionRecord.id)
                .limit(limit)
                .offset(offset)
            ).all()
            return [public(session, row) for row in rows]

    @app.get("/v1/projects/{project_id}/criteria/{criteria_id}")
    def status(project_id: UUID, criteria_id: UUID, auth=authorized):
        scope(auth, project_id, ("viewer",))
        with sessions() as session:
            return public(session, found(session, project_id, criteria_id))

    @app.post("/v1/projects/{project_id}/criteria/{criteria_id}/approve-generation")
    def approve_generation(
        project_id: UUID, criteria_id: UUID, body: CriteriaBudgetApproval, auth=authorized
    ):
        scope(auth, project_id, ("admin",))
        store = require_store(app)
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = found(session, project_id, criteria_id, lock=True)
            if row.state != "PENDING" or body.plan_hash != row.generation_plan_sha256:
                raise HTTPException(409, "CRITERIA_EXACT_PENDING_PLAN_HASH_REQUIRED")
            try:
                _, _, _, _, plan = prepared_generation(session, store, row)
            except ValueError:
                raise HTTPException(409, "CRITERIA_GENERATION_RIGHTS_OR_PLAN_UNAVAILABLE") from None
            if plan.approval_status != "pending":
                raise HTTPException(409, "CRITERIA_GENERATION_ALREADY_APPROVED_OR_CONSUMED")
            plan.approval_status = "approved"
            plan.approved_by = row.generation_approved_by = auth["actor"]
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="criteria_generation_budget_approved",
                    details={
                        "criteria_id": str(row.id),
                        "plan_hash": body.plan_hash,
                        "approval_ref": body.approval_ref,
                    },
                )
            )
            return public(session, row)

    @app.post("/v1/projects/{project_id}/criteria/{criteria_id}/runs", status_code=202)
    def queue(project_id: UUID, criteria_id: UUID, body: CriteriaQueueInput, auth=authorized):
        scope(auth, project_id, ("analyst",))
        store = require_store(app)
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = found(session, project_id, criteria_id, lock=True)
            if body.plan_hash != row.generation_plan_sha256:
                raise HTTPException(409, "CRITERIA_EXACT_PLAN_HASH_REQUIRED")
            if row.generation_run_id:
                return {**public(session, row), "existing_run": True, "requeued": False}
            try:
                _, _, _, _, planned = prepared_generation(session, store, row)
                if (
                    row.state != "PENDING"
                    or planned.approval_status != "approved"
                    or principal_role(session, project_id, row.generation_approved_by or "")
                    not in {"admin", "project_admin"}
                ):
                    raise ValueError
            except ValueError:
                raise HTTPException(
                    409, "CRITERIA_CURRENT_EXACT_BUDGET_APPROVAL_REQUIRED"
                ) from None
            run = ExperimentRunRecord(
                project_id=project_id,
                plan_id=planned.id,
                status="QUEUED",
                results={},
                call_ledger_version=1,
                known_cost_requests=0,
            )
            session.add(run)
            session.flush()
            session.add(
                JobRecord(
                    project_id=project_id,
                    run_id=run.id,
                    status="QUEUED",
                    execution_kind="criteria_generation",
                )
            )
            row.generation_run_id = run.id
            row.state = "QUEUED"
            planned.approval_status = "consumed"
            expose(
                session,
                project_id,
                row.dataset_id,
                "train",
                "criteria-generation:" + str(row.id),
                run_id=run.id,
            )
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="criteria_generation_queued",
                    details={"criteria_id": str(row.id), "run_id": str(run.id)},
                )
            )
            return {**public(session, row), "existing_run": False, "requeued": False}

    from cutback.server.evaluation_api import ContentPurpose

    @app.post("/v1/projects/{project_id}/criteria/{criteria_id}/content")
    def content(project_id: UUID, criteria_id: UUID, body: ContentPurpose, auth=authorized):
        from cutback.core.evaluation_criteria import ReviewedCriteriaBinding
        from cutback.evals.criteria_generation import materialize
        from cutback.server.criteria_content import current_policy, read_payload
        from cutback.server.criteria_service import read_input, reviewed_content

        scope(auth, project_id, ("admin",))
        store = require_store(app)
        with sessions.begin() as session:
            row = found(session, project_id, criteria_id)
            try:
                _, policy = current_policy(session, row)
                value, _, requirements = read_input(session, store, row, policy)
                proposal = None
                criteria = None
                invalid_output = None
                if row.state == "COMPLETED":
                    requirements, proposal, _ = reviewed_content(session, store, row)
                    criteria = materialize(proposal, requirements)
                elif row.state == "FAILED" and row.output_payload_id:
                    output, _ = read_payload(
                        session, store, row, row.output_payload_id, policy, kind="criteria_output"
                    )
                    if not isinstance(output, dict) or set(output) != {"output"}:
                        raise ValueError("CRITERIA_INVALID_OUTPUT_FRAME_CHANGED")
                    invalid_output = output["output"]
            except ValueError:
                raise HTTPException(409, "CRITERIA_CURRENT_CONTENT_UNAVAILABLE") from None
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="criteria_private_content_read",
                    details={"criteria_id": str(row.id), "purpose": body.purpose},
                )
            )
            binding = None
            if (
                row.review_status == "APPROVED"
                and row.review_approval_id
                and principal_role(session, project_id, row.reviewed_by or "")
                in {"admin", "project_admin"}
            ):
                binding = ReviewedCriteriaBinding(
                    criteria_id=row.id,
                    review_approval_id=row.review_approval_id,
                    version_sha256=row.version_sha256,
                ).model_dump(mode="json")
            return {
                **public(session, row),
                "requirements": requirements.model_dump(mode="json"),
                "training": value["training"],
                "proposal": proposal.model_dump(mode="json") if proposal else None,
                "criteria": [item.model_dump(mode="json") for item in criteria]
                if criteria
                else None,
                "criteria_binding": binding,
                "invalid_native_output": invalid_output,
                "review_assurance": "owner_review_not_independent_judge_calibration",
            }

    @app.post("/v1/projects/{project_id}/criteria/{criteria_id}/review")
    def review(project_id: UUID, criteria_id: UUID, body: CriteriaReviewInput, auth=authorized):
        from cutback.server.criteria_service import reviewed_content

        scope(auth, project_id, ("admin",))
        store = require_store(app)
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = found(session, project_id, criteria_id, lock=True)
            if row.version_sha256 != body.version_sha256:
                raise HTTPException(409, "CRITERIA_EXACT_CONTENT_VERSION_REQUIRED")
            try:
                reviewed_content(session, store, row)
            except ValueError:
                raise HTTPException(
                    409, "CRITERIA_COMPLETE_KNOWN_GENERATION_AND_CURRENT_RIGHTS_REQUIRED"
                ) from None
            decision = "APPROVED" if body.decision == "approve" else "REJECTED"
            if row.review_status == decision and row.reviewed_by == auth["actor"]:
                return public(session, row)
            if row.review_approval_id != body.expected_review_approval_id:
                raise HTTPException(409, "CRITERIA_REVIEW_CHANGED_RECONCILE_CURRENT_VERSION")
            previous = row.review_approval_id
            row.review_status = decision
            row.reviewed_by = auth["actor"]
            row.reviewed_at = datetime.now(UTC)
            row.review_approval_id = uuid4()
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="criteria_version_reviewed",
                    details={
                        "criteria_id": str(row.id),
                        "version_sha256": row.version_sha256,
                        "decision": decision,
                        "approval_ref": body.approval_ref,
                        "review_approval_id": str(row.review_approval_id),
                        "previous_review_approval_id": str(previous) if previous else None,
                    },
                )
            )
            return public(session, row)

    @app.post(
        "/v1/projects/{project_id}/criteria/{criteria_id}/recommendations/{recommendation_id}/source-evidence"
    )
    def source_evidence(
        project_id: UUID,
        criteria_id: UUID,
        recommendation_id: UUID,
        body: ContentPurpose,
        auth=authorized,
    ):
        from cutback.server.criteria_service import validate_binding
        from cutback.server.dataset_lineage import require_current_test_evidence
        from cutback.server.db import RecommendationRecord
        from cutback.server.plan_artifacts import load_plan

        scope(auth, project_id, ("admin",))
        store = require_store(app)
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            found(session, project_id, criteria_id)
            rec = session.get(RecommendationRecord, recommendation_id)
            if not rec or rec.project_id != project_id:
                raise HTTPException(404, "RECOMMENDATION_NOT_FOUND")
            run = session.get(ExperimentRunRecord, rec.run_id)
            planned = session.get(ExperimentPlanRecord, run.plan_id)
            try:
                plan = load_plan(store, project_id, planned.specification)
                if (
                    not plan.criteria_binding
                    or plan.criteria_binding.criteria_id != criteria_id
                    or not plan.source_binding
                    or rec.evidence.get("plan_hash") != planned.specification["plan_hash"]
                    or not rec.evidence.get("quality_gate_passed")
                    or not rec.evidence.get("cheaper_on_sample")
                ):
                    raise ValueError
                validate_binding(session, store, project_id, plan)
                require_current_test_evidence(session, store, rec)
                candidate = next(
                    item for item in plan.candidates if item.key == rec.evidence["candidate"]["key"]
                )
            except (ValueError, StopIteration, KeyError):
                raise HTTPException(409, "CURRENT_REVIEWED_SOURCE_CONFIRMATION_REQUIRED") from None
            evidence = {
                **rec.evidence,
                "candidate": candidate.model_dump(mode="json"),
                "rollback": {
                    **rec.evidence.get("rollback", {}),
                    "baseline": plan.baseline.model_dump(mode="json"),
                },
            }
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="criteria_source_evidence_read",
                    details={
                        "criteria_id": str(criteria_id),
                        "recommendation_id": str(rec.id),
                        "purpose": body.purpose,
                    },
                )
            )
            return {
                "id": str(rec.id),
                "run_id": str(run.id),
                "status": rec.status,
                "evidence": evidence,
            }

    @app.get("/v1/projects/{project_id}/criteria/{criteria_id}/evidence-validity")
    def evidence_validity(project_id: UUID, criteria_id: UUID, run_id: UUID, auth=authorized):
        from cutback.server.criteria_service import validate_binding
        from cutback.server.dataset_lineage import require_current_test_evidence
        from cutback.server.db import RecommendationRecord
        from cutback.server.plan_artifacts import load_plan

        scope(auth, project_id, ("viewer",))
        store = require_store(app)
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            found(session, project_id, criteria_id)
            run = session.get(ExperimentRunRecord, run_id)
            if not run or run.project_id != project_id:
                raise HTTPException(404, "RUN_NOT_FOUND")
            try:
                planned = session.get(ExperimentPlanRecord, run.plan_id)
                plan = load_plan(store, project_id, planned.specification)
                if (
                    not plan.criteria_binding
                    or plan.criteria_binding.criteria_id != criteria_id
                    or not plan.predecessor_run_id
                ):
                    raise ValueError
                validate_binding(session, store, project_id, plan)
                recommendations = session.scalars(
                    select(RecommendationRecord).where(
                        RecommendationRecord.project_id == project_id,
                        RecommendationRecord.run_id == run.id,
                    )
                ).all()
                if len(recommendations) != 1:
                    raise ValueError
                require_current_test_evidence(session, store, recommendations[0])
            except ValueError:
                return {
                    "eligible": False,
                    "code": "CURRENT_REVIEWED_CONFIRMATION_UNAVAILABLE",
                    "provider_calls": 0,
                    "historical_quality_recomputed": False,
                }
            return {
                "eligible": True,
                "criteria_binding": plan.criteria_binding.model_dump(mode="json"),
                "provider_calls": 0,
                "historical_quality_recomputed": False,
            }

    @app.post("/v1/projects/{project_id}/criteria/{criteria_id}/cancel")
    def cancel(project_id: UUID, criteria_id: UUID, body: CriteriaCancelInput, auth=authorized):
        scope(auth, project_id, ("analyst",))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = found(session, project_id, criteria_id, lock=True)
            if row.state in {"COMPLETED", "FAILED", "CANCELLED"}:
                return public(session, row)
            row.state = "CANCELLED"
            plan = session.get(ExperimentPlanRecord, row.generation_plan_id, with_for_update=True)
            if plan.approval_status in {"pending", "approved"}:
                plan.approval_status = "rejected"
            if row.generation_run_id:
                run = session.get(ExperimentRunRecord, row.generation_run_id, with_for_update=True)
                job = session.scalar(
                    select(JobRecord).where(JobRecord.run_id == run.id).with_for_update()
                )
                if run.status in {"QUEUED", "BLOCKED", "RUNNING"}:
                    run.status = "CANCELLED"
                    run.results = {**run.results, "code": "CRITERIA_GENERATION_CANCELLED_NO_REPLAY"}
                    job.status = "CANCELLED"
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="criteria_generation_cancelled",
                    details={"criteria_id": str(row.id), "reason": body.reason},
                )
            )
            return public(session, row)

    @app.post("/v1/projects/{project_id}/criteria/{criteria_id}/evaluation-draft", status_code=201)
    def evaluation_draft(
        project_id: UUID, criteria_id: UUID, body: CriteriaEvaluationInput, auth=authorized
    ):
        from cutback.evals.criteria_generation import materialize
        from cutback.server.criteria_service import reviewed_content, validate_binding
        from cutback.server.evaluation_api import PlanInput

        scope(auth, project_id, ("analyst",))
        store = require_store(app)
        with sessions() as session:
            row = found(session, project_id, criteria_id)
            dataset = session.get(DatasetRecord, body.dataset_id)
            if not dataset or dataset.project_id != project_id:
                raise HTTPException(404, "DATASET_NOT_FOUND")
            try:
                if body.criteria_binding.criteria_id != criteria_id:
                    raise ValueError
                if (body.request_id is None) != (body.content_nonce is None):
                    raise ValueError
                requirements, proposal, _ = reviewed_content(session, store, row)
                criteria = materialize(proposal, requirements)
                providers: list[Literal["openai", "anthropic", "local"]] = []
                for target in [body.baseline, *body.candidates]:
                    if target.provider != "openai" and target.provider != "anthropic":
                        raise ValueError
                    if target.provider not in providers:
                        providers.append(target.provider)
                plan = ExperimentPlan(
                    name=body.name,
                    workflow=dataset.workflow_id,
                    dataset_hash=dataset.immutable_hash,
                    baseline=body.baseline,
                    candidates=body.candidates,
                    metric=criteria[0].metric,
                    criteria=criteria,
                    criteria_binding=body.criteria_binding,
                    evaluator_version="deterministic-v13",
                    content_nonce=body.content_nonce or uuid4(),
                    split="validation",
                    budget=body.budget,
                    repeats=body.repeats,
                    retain_case_outputs=body.retain_case_outputs,
                    source_binding=body.source_binding,
                    approved_providers=sorted(providers),
                )
                validate_binding(session, store, project_id, plan)
            except ValueError:
                raise HTTPException(
                    409, "CURRENT_REVIEWED_CRITERIA_AND_NATIVE_TARGET_CONTRACT_REQUIRED"
                ) from None
        return create_plan(
            project_id,
            PlanInput(request_id=body.request_id, dataset_id=body.dataset_id, specification=plan),
            auth,
        )

    @app.post("/v1/projects/{project_id}/criteria/{criteria_id}/confirmation", status_code=201)
    def confirmation(
        project_id: UUID, criteria_id: UUID, body: CriteriaConfirmationInput, auth=authorized
    ):
        from cutback.server.criteria_service import validate_binding
        from cutback.server.evaluation_api import PlanInput
        from cutback.server.plan_artifacts import load_plan
        from cutback.server.stages import validate_confirmation

        scope(auth, project_id, ("analyst",))
        store = require_store(app)
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            found(session, project_id, criteria_id)
            run = session.get(ExperimentRunRecord, body.validation_run_id)
            if not run or run.project_id != project_id or run.status != "COMPLETED":
                raise HTTPException(404, "COMPLETED_VALIDATION_NOT_FOUND")
            planned = session.get(ExperimentPlanRecord, run.plan_id)
            try:
                prior = load_plan(store, project_id, planned.specification)
                if not prior.criteria_binding or prior.criteria_binding.criteria_id != criteria_id:
                    raise ValueError
                selected = next(m for m in prior.candidates if m.key == body.candidate_key)
                raw = prior.model_dump(mode="json")
                raw.update(
                    name="reviewed-criteria-confirmation",
                    candidates=[selected.model_dump(mode="json")],
                    split="test",
                    sample_limit=None,
                    predecessor_run_id=str(run.id),
                    budget=body.budget.model_dump(mode="json"),
                )
                plan = ExperimentPlan.model_validate(raw)
                validate_binding(session, store, project_id, plan)
                validate_confirmation(session, store, project_id, plan)
            except (ValueError, StopIteration):
                raise HTTPException(
                    409, "EXACT_REVIEWED_VALIDATION_CANDIDATE_AND_CONFIRMATION_GATES_REQUIRED"
                ) from None
            dataset_id = planned.dataset_id
        return create_plan(project_id, PlanInput(dataset_id=dataset_id, specification=plan), auth)
