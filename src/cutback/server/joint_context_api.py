"""Frozen joint model/context search using the canonical approval and call ledger."""

from uuid import UUID

from fastapi import Depends, HTTPException
from pydantic import Field

from cutback.core.compression import CompressionConfigurationBinding, CompressionSettings
from cutback.core.context_producer import ContextProducerBinding
from cutback.core.models import StrictModel
from cutback.evals.experiments import Budget, ModelSpec
from cutback.evals.metrics import MetricSpec
from cutback.evals.retrieval import AssumedCPURate
from cutback.joint_context_changes import JointContextProposal
from cutback.server.db import AuditEvent, DatasetRecord, ExperimentPlanRecord, ExperimentRunRecord
from cutback.server.plan_artifacts import load_plan


class JointContextDraftInput(StrictModel):
    dataset_id: UUID
    baseline: ModelSpec
    settings: CompressionSettings
    metric: MetricSpec
    budget: Budget
    models: list[ModelSpec] | None = Field(default=None, max_length=5)
    contexts: list[CompressionSettings] | None = Field(default=None, min_length=1, max_length=5)
    max_candidates: int = Field(default=5, strict=True, ge=1, le=5)
    assumed_usd_per_cpu_hour: AssumedCPURate | None = None
    max_query_bytes: int = Field(default=1024, strict=True, ge=1, le=4096)
    retain_case_outputs: bool = False
    configuration_binding: CompressionConfigurationBinding | None = None
    producer_binding: ContextProducerBinding | None = None


class JointContextConfirmationInput(StrictModel):
    validation_run_id: UUID
    budget: Budget


def install_joint_context(app, sessions, authorize, scope, create_plan):
    from cutback.server.evaluation_api import (
        ContentPurpose,
        PlanInput,
        active_policy,
        require_store,
    )

    authorized = Depends(authorize)

    @app.post("/v1/projects/{project_id}/model-context/draft", status_code=201)
    def draft(project_id: UUID, body: JointContextDraftInput, auth=authorized):
        from cutback.evals.joint_context_planning import draft_joint_plan

        scope(auth, project_id, ("analyst",))
        require_store(app)
        with sessions() as session:
            dataset = session.get(DatasetRecord, body.dataset_id)
            if not dataset or dataset.project_id != project_id:
                raise HTTPException(404, "DATASET_NOT_FOUND")
            metadata = {
                "hash": dataset.immutable_hash,
                "workflow": dataset.workflow_id,
                "provenance": dataset.provenance,
            }
        try:
            plan = draft_joint_plan(
                metadata,
                body.baseline,
                body.settings,
                metric=body.metric,
                budget=body.budget,
                **{
                    name: getattr(body, name)
                    for name in type(body).model_fields
                    if name not in {"dataset_id", "baseline", "settings", "metric", "budget"}
                },
            )
        except ValueError:
            raise HTTPException(422, "JOINT_MODEL_CONTEXT_DRAFT_CONTRACT_INVALID") from None
        return create_plan(
            project_id, PlanInput(dataset_id=body.dataset_id, specification=plan), auth
        )

    @app.post("/v1/projects/{project_id}/model-context/confirmation", status_code=201)
    def confirm(project_id: UUID, body: JointContextConfirmationInput, auth=authorized):
        from cutback.evals.joint_context_planning import confirmation_plan

        scope(auth, project_id, ("analyst",))
        store = require_store(app)
        with sessions() as session:
            run = session.get(ExperimentRunRecord, body.validation_run_id)
            if not run or run.project_id != project_id or run.status != "COMPLETED":
                raise HTTPException(404, "COMPLETED_VALIDATION_NOT_FOUND")
            row = session.get(ExperimentPlanRecord, run.plan_id)
            active_policy(session, project_id, UUID(row.specification["policy_id"]))
            try:
                plan = confirmation_plan(
                    load_plan(store, project_id, row.specification),
                    run.results,
                    str(run.id),
                    body.budget,
                )
            except ValueError:
                raise HTTPException(
                    409, "JOINT_MODEL_CONTEXT_CONFIRMATION_GATES_NOT_PASSED"
                ) from None
            dataset_id = row.dataset_id
        return create_plan(project_id, PlanInput(dataset_id=dataset_id, specification=plan), auth)

    @app.post(
        "/v1/projects/{project_id}/model-context/{run_id}/proposal",
        response_model=JointContextProposal,
    )
    def proposal(project_id: UUID, run_id: UUID, body: ContentPurpose, auth=authorized):
        from cutback.evals.compression import JointModelContextExecutionSpec
        from cutback.server.dataset_lineage import lock_lineage
        from cutback.server.stages import validate_confirmation

        scope(auth, project_id, ("admin",))
        store = require_store(app)
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            run = session.get(ExperimentRunRecord, run_id)
            if not run or run.project_id != project_id or run.status != "COMPLETED":
                raise HTTPException(404, "COMPLETED_CONFIRMATION_NOT_FOUND")
            row = session.get(ExperimentPlanRecord, run.plan_id)
            active_policy(session, project_id, UUID(row.specification["policy_id"]))
            try:
                plan = load_plan(store, project_id, row.specification)
                if (
                    not isinstance(plan.compression_execution, JointModelContextExecutionSpec)
                    or not plan.predecessor_run_id
                ):
                    raise ValueError("JOINT_CONFIRMATION_REQUIRED")
                validate_confirmation(session, store, project_id, plan, run_id=run.id)
            except ValueError:
                raise HTTPException(409, "JOINT_CONFIRMATION_REQUIRED") from None
            decision: dict = next(iter(run.results.get("recommendations", [])), {})
            if (
                not run.results.get("billing_complete")
                or decision.get("joint_model_context_confirmation_eligible") is not True
                or run.results.get("lineage", {}).get("fresh_test_claim") is not True
            ):
                raise HTTPException(409, "JOINT_CONFIRMATION_GATES_NOT_PASSED")
            spec = plan.compression_execution
            if not spec.configuration_binding or not spec.producer_binding:
                raise HTTPException(409, "JOINT_QUALIFIED_SOURCE_PRODUCER_REQUIRED")
            dataset = session.get(DatasetRecord, row.dataset_id)
            result = JointContextProposal(
                run_id=run.id,
                plan_hash=row.specification["plan_hash"],
                dataset_hash=plan.dataset_hash,
                binding=spec.configuration_binding,
                producer=spec.producer_binding,
                baseline=plan.baseline,
                proposed=plan.candidates[0],
                proposed_settings=spec.variants[1].settings,
                decision="synthetic_local_review_only"
                if dataset.provenance["source_type"] == "synthetic_fixture"
                else "heldout_review_required"
                if decision["status"] == "VERIFIED_ON_HELDOUT_DATASET"
                else "sample_review_required",
            )
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="joint_model_context_proposal_read",
                    details={"run_id": str(run.id), "purpose": body.purpose},
                )
            )
            return result
