"""Joint native/retrieval plans use existing scoped approvals, storage and reservations."""

from uuid import UUID

from fastapi import Depends, HTTPException
from pydantic import Field

from cutback.core.models import Identifier, StrictModel
from cutback.core.retrieval import RetrievalConfigurationBinding, RetrievalCorpus, RetrievalSettings
from cutback.core.retrieval_producer import RetrievalProducerBinding
from cutback.evals.experiments import Budget, ModelSpec
from cutback.evals.metrics import MetricSpec
from cutback.evals.retrieval import AssumedCPURate
from cutback.evals.retrieval_joint import JOINT_RETRIEVAL_CONTRACT, JointModelRetrievalExecutionSpec
from cutback.joint_retrieval_changes import JointRetrievalProposal
from cutback.server.db import AuditEvent, DatasetRecord, ExperimentPlanRecord, ExperimentRunRecord
from cutback.server.plan_artifacts import load_plan


class JointRetrievalDraftInput(StrictModel):
    dataset_id: UUID
    baseline: ModelSpec
    corpus: RetrievalCorpus
    settings: RetrievalSettings
    metric: MetricSpec
    budget: Budget
    max_candidates: int = Field(default=5, strict=True, ge=1, le=5)
    assumed_usd_per_cpu_hour: AssumedCPURate | None = None
    max_query_bytes: int = Field(default=1024, strict=True, ge=1, le=8192)
    retain_case_outputs: bool = False
    configuration_binding: RetrievalConfigurationBinding | None = None
    producer_binding: RetrievalProducerBinding | None = None


class JointRetrievalConfirmationInput(StrictModel):
    validation_run_id: UUID
    budget: Budget
    candidate: Identifier | None = None


def install_joint_retrieval(app, sessions, authorize, scope, create_plan):
    from cutback.server.evaluation_api import (
        ContentPurpose,
        PlanInput,
        active_policy,
        require_store,
    )

    authorized = Depends(authorize)

    @app.post("/v1/projects/{project_id}/model-retrieval/draft", status_code=201)
    def draft(project_id: UUID, body: JointRetrievalDraftInput, auth=authorized):
        from cutback.evals.joint_retrieval_planning import draft_plan

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
            plan = draft_plan(
                metadata,
                body.baseline,
                body.corpus,
                body.settings,
                metric=body.metric,
                budget=body.budget,
                **{
                    name: getattr(body, name)
                    for name in type(body).model_fields
                    if name
                    not in {"dataset_id", "baseline", "corpus", "settings", "metric", "budget"}
                },
            )
        except ValueError:
            raise HTTPException(422, "JOINT_RETRIEVAL_DRAFT_CONTRACT_INVALID") from None
        return create_plan(
            project_id, PlanInput(dataset_id=body.dataset_id, specification=plan), auth
        )

    @app.post("/v1/projects/{project_id}/model-retrieval/confirmation", status_code=201)
    def confirm(project_id: UUID, body: JointRetrievalConfirmationInput, auth=authorized):
        from cutback.evals.joint_retrieval_planning import confirmation_plan

        scope(auth, project_id, ("analyst",))
        store = require_store(app)
        with sessions() as session:
            run = session.get(ExperimentRunRecord, body.validation_run_id)
            if not run or run.project_id != project_id or run.status != "COMPLETED":
                raise HTTPException(404, "COMPLETED_VALIDATION_NOT_FOUND")
            row = session.get(ExperimentPlanRecord, run.plan_id)
            active_policy(session, project_id, UUID(row.specification["policy_id"]))
            try:
                metadata = row.specification.get("public_specification", {}).get(
                    "retrieval_execution"
                )
                if (
                    not isinstance(metadata, dict)
                    or metadata.get("version") != JOINT_RETRIEVAL_CONTRACT
                ):
                    raise ValueError("JOINT_RETRIEVAL_ROUTE_CONTRACT_MISMATCH")
                plan = confirmation_plan(
                    load_plan(store, project_id, row.specification),
                    run.results,
                    str(run.id),
                    body.budget,
                    candidate=body.candidate,
                )
            except ValueError:
                raise HTTPException(409, "JOINT_RETRIEVAL_CONFIRMATION_GATES_NOT_PASSED") from None
            dataset_id = row.dataset_id
        return create_plan(project_id, PlanInput(dataset_id=dataset_id, specification=plan), auth)

    @app.post(
        "/v1/projects/{project_id}/model-retrieval/{run_id}/proposal",
        response_model=JointRetrievalProposal,
    )
    def proposal(project_id: UUID, run_id: UUID, body: ContentPurpose, auth=authorized):
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
                metadata = row.specification.get("public_specification", {}).get(
                    "retrieval_execution"
                )
                if (
                    not isinstance(metadata, dict)
                    or metadata.get("version") != JOINT_RETRIEVAL_CONTRACT
                ):
                    raise ValueError("JOINT_RETRIEVAL_ROUTE_CONTRACT_MISMATCH")
                if not metadata.get("configuration_binding") or not metadata.get(
                    "producer_binding"
                ):
                    raise ValueError("JOINT_RETRIEVAL_QUALIFIED_SOURCE_PRODUCER_REQUIRED")
                plan = load_plan(store, project_id, row.specification)
                if (
                    not isinstance(plan.retrieval_execution, JointModelRetrievalExecutionSpec)
                    or not plan.predecessor_run_id
                ):
                    raise ValueError("JOINT_RETRIEVAL_CONFIRMATION_REQUIRED")
                validate_confirmation(session, store, project_id, plan, run_id=run.id)
            except ValueError:
                raise HTTPException(409, "JOINT_RETRIEVAL_CONFIRMATION_REQUIRED") from None
            decision: dict = next(iter(run.results.get("recommendations", [])), {})
            if (
                run.results.get("billing_complete") is not True
                or decision.get("joint_model_retrieval_confirmation_eligible") is not True
                or run.results.get("lineage", {}).get("fresh_test_claim") is not True
            ):
                raise HTTPException(409, "JOINT_RETRIEVAL_CONFIRMATION_GATES_NOT_PASSED")
            spec = plan.retrieval_execution
            if not spec.configuration_binding or not spec.producer_binding:
                raise HTTPException(409, "JOINT_RETRIEVAL_QUALIFIED_SOURCE_PRODUCER_REQUIRED")
            dataset = session.get(DatasetRecord, row.dataset_id)
            try:
                result = JointRetrievalProposal(
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
            except ValueError:
                raise HTTPException(409, "JOINT_RETRIEVAL_SOURCE_CHANGE_NOT_QUALIFIED") from None
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="joint_model_retrieval_proposal_read",
                    details={"run_id": str(run.id), "purpose": body.purpose},
                )
            )
            return result
