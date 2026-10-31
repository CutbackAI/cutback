"""One approved scenario-generation call, explicit owner review, then pinned checks."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException, Query
from pydantic import UUID4, Field
from sqlalchemy import func, select

from cutback.core.models import Identifier, StrictModel
from cutback.evals.experiments import upper_cost
from cutback.evals.scenario_generation import (
    ScenarioGenerationPlan,
    ScenarioPreparation,
    generation_input,
    generator_model,
)
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
from cutback.server.scenario_db import ScenarioVersionRecord


class ScenarioDraftInput(ScenarioPreparation):
    pass


class ScenarioBudgetApproval(StrictModel):
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    approval_ref: Identifier


class ScenarioQueueInput(StrictModel):
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ScenarioReviewInput(StrictModel):
    version_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["approve", "reject"]
    approval_ref: Identifier
    expected_review_approval_id: UUID | None = None


class ScenarioCancelInput(StrictModel):
    reason: Identifier


class ScenarioImportInput(StrictModel):
    request_id: UUID4
    version_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    review_approval_id: UUID4
    approval_ref: Identifier


def install_scenario(app, sessions, authorize, scope):
    from cutback.server.campaign_content import content_transaction
    from cutback.server.dataset_lineage import expose, lock_lineage
    from cutback.server.evaluation_api import active_policy, policy_object_count, require_store
    from cutback.server.plan_artifacts import freeze_plan
    from cutback.server.principals import principal_role
    from cutback.server.scenario_content import put_payload, training_view
    from cutback.server.scenario_service import prepared_generation

    authorized = Depends(authorize)

    from cutback.server.evaluation_api import ContentPurpose

    @app.post("/v1/projects/{project_id}/scenarios/{scenario_id}/content")
    def content(project_id: UUID, scenario_id: UUID, body: ContentPurpose, auth=authorized):
        from cutback.server.scenario_content import current_policy, read_payload
        from cutback.server.scenario_service import read_input, reviewed_content

        scope(auth, project_id, ("admin",))
        store = require_store(app)
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = found(session, project_id, scenario_id)
            try:
                _, policy = current_policy(session, row)
                value, _, requirements = read_input(session, store, row, policy)
                proposal = preview = invalid_output = None
                if row.state == "COMPLETED":
                    requirements, proposal, preview = reviewed_content(session, store, row)
                elif row.state == "FAILED" and row.output_payload_id:
                    output, _ = read_payload(
                        session, store, row, row.output_payload_id, policy, kind="scenario_output"
                    )
                    if not isinstance(output, dict) or set(output) != {"output"}:
                        raise ValueError
                    invalid_output = output["output"]
            except ValueError:
                raise HTTPException(409, "SCENARIO_CURRENT_CONTENT_UNAVAILABLE") from None
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="scenario_private_content_read",
                    details={"scenario_id": str(row.id), "purpose": body.purpose},
                )
            )
            return {
                **public(session, row),
                "requirements": requirements.model_dump(mode="json"),
                "training": value["training"],
                "proposal": proposal.model_dump(mode="json") if proposal else None,
                "preview": preview,
                "invalid_native_output": invalid_output,
                "review_assurance": "owner_review_not_independent_labels_or_population_coverage",
            }

    @app.post("/v1/projects/{project_id}/scenarios/{scenario_id}/imports", status_code=201)
    def import_reviewed(
        project_id: UUID, scenario_id: UUID, body: ScenarioImportInput, auth=authorized
    ):
        from cutback.evals.datasets import DatasetImport, digest
        from cutback.server.dataset_lineage import lock_preparation_identity
        from cutback.server.evaluation_api import persist_dataset
        from cutback.server.scenario_db import ScenarioDatasetImport
        from cutback.server.scenario_service import require_current_import, reviewed_content

        scope(auth, project_id, ("admin",))
        store = require_store(app)
        with content_transaction(sessions, store, project_id) as (session, refs):
            lock_lineage(session, project_id)
            lock_preparation_identity(session, "scenario-import", body.request_id)
            row = found(session, project_id, scenario_id, lock=True)
            identity = digest(
                {"scenario_id": str(scenario_id), "body": body.model_dump(mode="json")}
            )
            previous = session.scalar(
                select(ScenarioDatasetImport).where(
                    ScenarioDatasetImport.request_id == body.request_id
                )
            )
            if previous:
                if (
                    previous.project_id != project_id
                    or previous.scenario_id != scenario_id
                    or previous.request_sha256 != identity
                ):
                    raise HTTPException(409, "SCENARIO_IMPORT_REQUEST_ID_UNAVAILABLE_OR_CHANGED")
                target = session.get(DatasetRecord, previous.dataset_id)
                try:
                    require_current_import(session, store, target)
                except ValueError:
                    raise HTTPException(409, "SCENARIO_IMPORT_CURRENT_REVIEW_REQUIRED") from None
                return {
                    "dataset_id": str(target.id),
                    "immutable_hash": target.immutable_hash,
                    "existing_import": True,
                    "development_only": True,
                    "provider_calls": 0,
                }
            if session.get(DatasetRecord, body.request_id) is not None:
                raise HTTPException(409, "SCENARIO_IMPORT_REQUEST_ID_UNAVAILABLE_OR_CHANGED")
            if (
                row.review_status != "APPROVED"
                or row.version_sha256 != body.version_sha256
                or row.review_approval_id != body.review_approval_id
                or principal_role(session, project_id, row.reviewed_by or "")
                not in {"admin", "project_admin"}
            ):
                raise HTTPException(409, "SCENARIO_EXACT_CURRENT_REVIEW_REQUIRED")
            try:
                _, _, preview = reviewed_content(session, store, row)
                source = session.get(DatasetRecord, row.dataset_id)
                imported = DatasetImport(
                    workflow=source.workflow_id,
                    environment=source.provenance["environment"],
                    policy_id=str(row.capture_policy_id),
                    source_type=source.provenance["source_type"],
                    independent_labels=False,
                    representative=False,
                    provenance_ref="reviewed-generated-scenarios",
                    cases=preview["cases"],
                )
                persisted = persist_dataset(
                    session,
                    store,
                    project_id,
                    imported,
                    auth["actor"],
                    refs,
                    development_only=True,
                    dataset_id=body.request_id,
                    provenance_extra={
                        "generated_scenario": {
                            "scenario_id": str(row.id),
                            "version_sha256": row.version_sha256,
                            "review_approval_id": str(row.review_approval_id),
                            "development_only": True,
                        }
                    },
                )
                target = session.get(DatasetRecord, UUID(persisted["id"]))
                payloads = session.scalars(
                    select(PayloadSample)
                    .join(DatasetItem, DatasetItem.payload_id == PayloadSample.id)
                    .where(DatasetItem.dataset_id == target.id)
                ).all()
                for payload in payloads:
                    payload.expires_at = min(payload.expires_at, row.expires_at)
            except ValueError:
                raise HTTPException(
                    409, "SCENARIO_CURRENT_REVIEW_OR_IMPORT_CONTRACT_UNAVAILABLE"
                ) from None
            session.add(
                ScenarioDatasetImport(
                    project_id=project_id,
                    dataset_id=target.id,
                    scenario_id=row.id,
                    request_id=body.request_id,
                    request_sha256=identity,
                    version_sha256=row.version_sha256,
                    review_approval_id=row.review_approval_id,
                )
            )
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="scenario_development_dataset_imported",
                    details={
                        "scenario_id": str(row.id),
                        "dataset_id": str(target.id),
                        "version_sha256": row.version_sha256,
                        "review_approval_id": str(row.review_approval_id),
                        "approval_ref": body.approval_ref,
                    },
                )
            )
            return {
                "dataset_id": str(target.id),
                "immutable_hash": target.immutable_hash,
                "existing_import": False,
                "development_only": True,
                "provider_calls": 0,
            }

    def found(session, project, identifier, *, lock=False):
        row = session.get(
            ScenarioVersionRecord, identifier, with_for_update=lock, populate_existing=True
        )
        if not row or row.project_id != project:
            raise HTTPException(404, "SCENARIO_VERSION_NOT_FOUND")
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
            "scenario_kind": "task_scenarios_v1",
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
            "proposed_scenarios_count": run.results.get("proposed_scenarios_count")
            if run
            else None,
            "code": run.results.get("code") if run else None,
            "quality_evidence": "none_proposal_and_owner_review_only",
        }

    @app.post("/v1/projects/{project_id}/scenarios/drafts", status_code=201)
    def draft(project_id: UUID, body: ScenarioDraftInput, auth=authorized):
        scope(auth, project_id, ("analyst",))
        store = require_store(app)
        with content_transaction(sessions, store, project_id) as (session, refs):
            lock_lineage(session, project_id)
            if body.request_id:
                from cutback.server.dataset_lineage import lock_preparation_identity

                lock_preparation_identity(session, "scenario-draft", body.request_id)
                previous = session.get(ScenarioVersionRecord, body.request_id, with_for_update=True)
                if previous:
                    if previous.project_id != project_id:
                        raise HTTPException(409, "SCENARIO_REQUEST_ID_UNAVAILABLE")
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
                            or {key: case.model_dump(mode="json") for key, case in training.items()}
                            != value["training"]
                            or identities != value["source_item_ids"]
                        ):
                            raise ValueError
                    except ValueError:
                        raise HTTPException(
                            409, "SCENARIO_REQUEST_ID_CONTENT_OR_RIGHTS_CHANGED"
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
                raise HTTPException(429, "SCENARIO_INPUT_AND_OUTPUT_RESERVATION_QUOTA")
            if "openai" not in policy.allowed_providers:
                raise HTTPException(403, "SCENARIO_GENERATOR_VENDOR_NOT_AUTHORIZED")
            environment = session.scalar(
                select(Environment).where(
                    Environment.project_id == project_id,
                    Environment.name == dataset.provenance["environment"],
                )
            )
            if not environment:
                raise HTTPException(409, "SCENARIO_ENVIRONMENT_NOT_REGISTERED")
            for model in (ScenarioVersionRecord, ExperimentPlanRecord):
                if (
                    session.scalar(
                        select(func.count())
                        .select_from(model)
                        .where(model.project_id == project_id)
                    )
                    >= 1000
                ):
                    raise HTTPException(429, "SCENARIO_PROJECT_PLAN_OR_VERSION_QUOTA")
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
                    raise ValueError("SCENARIO_ONE_CALL_INPUT_OR_BUDGET_BOUND")
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
                row = ScenarioVersionRecord(
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
                        "training": {
                            key: case.model_dump(mode="json") for key, case in training.items()
                        },
                        "source_item_ids": source_ids,
                    },
                    "scenario_input",
                    refs,
                    policy,
                )
                row.input_payload_id = payload.id
                plan = ScenarioGenerationPlan(
                    scenario_id=row.id,
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
                    session, project_id, dataset.id, "train", "scenario-preparation:" + str(row.id)
                )
                session.add(
                    AuditEvent(
                        project_id=project_id,
                        actor=auth["actor"],
                        action="scenario_generation_planned",
                        details={
                            "scenario_id": str(row.id),
                            "training_rows": len(training),
                            "plan_hash": metadata["plan_hash"],
                        },
                    )
                )
                session.flush()
            except ValueError:
                raise HTTPException(
                    422, "SCENARIO_TRAINING_GENERATOR_OR_BUDGET_NOT_QUALIFIED"
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

    @app.get("/v1/projects/{project_id}/scenarios")
    def list_versions(
        project_id: UUID,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
        auth=authorized,
    ):
        scope(auth, project_id, ("viewer",))
        with sessions() as session:
            rows = session.scalars(
                select(ScenarioVersionRecord)
                .where(ScenarioVersionRecord.project_id == project_id)
                .order_by(ScenarioVersionRecord.created_at.desc(), ScenarioVersionRecord.id)
                .limit(limit)
                .offset(offset)
            ).all()
            return [public(session, row) for row in rows]

    @app.get("/v1/projects/{project_id}/scenarios/{scenario_id}")
    def status(project_id: UUID, scenario_id: UUID, auth=authorized):
        scope(auth, project_id, ("viewer",))
        with sessions() as session:
            return public(session, found(session, project_id, scenario_id))

    @app.post("/v1/projects/{project_id}/scenarios/{scenario_id}/approve-generation")
    def approve_generation(
        project_id: UUID, scenario_id: UUID, body: ScenarioBudgetApproval, auth=authorized
    ):
        scope(auth, project_id, ("admin",))
        store = require_store(app)
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = found(session, project_id, scenario_id, lock=True)
            if row.state != "PENDING" or body.plan_hash != row.generation_plan_sha256:
                raise HTTPException(409, "SCENARIO_EXACT_PENDING_PLAN_HASH_REQUIRED")
            try:
                _, _, _, _, plan = prepared_generation(session, store, row)
            except ValueError:
                raise HTTPException(409, "SCENARIO_GENERATION_RIGHTS_OR_PLAN_UNAVAILABLE") from None
            if plan.approval_status != "pending":
                raise HTTPException(409, "SCENARIO_GENERATION_ALREADY_APPROVED_OR_CONSUMED")
            plan.approval_status = "approved"
            plan.approved_by = row.generation_approved_by = auth["actor"]
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="scenario_generation_budget_approved",
                    details={
                        "scenario_id": str(row.id),
                        "plan_hash": body.plan_hash,
                        "approval_ref": body.approval_ref,
                    },
                )
            )
            return public(session, row)

    @app.post("/v1/projects/{project_id}/scenarios/{scenario_id}/runs", status_code=202)
    def queue(project_id: UUID, scenario_id: UUID, body: ScenarioQueueInput, auth=authorized):
        scope(auth, project_id, ("analyst",))
        store = require_store(app)
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = found(session, project_id, scenario_id, lock=True)
            if body.plan_hash != row.generation_plan_sha256:
                raise HTTPException(409, "SCENARIO_EXACT_PLAN_HASH_REQUIRED")
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
                    409, "SCENARIO_CURRENT_EXACT_BUDGET_APPROVAL_REQUIRED"
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
                    execution_kind="scenario_generation",
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
                "scenario-generation:" + str(row.id),
                run_id=run.id,
            )
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="scenario_generation_queued",
                    details={"scenario_id": str(row.id), "run_id": str(run.id)},
                )
            )
            return {**public(session, row), "existing_run": False, "requeued": False}

    @app.post("/v1/projects/{project_id}/scenarios/{scenario_id}/review")
    def review(project_id: UUID, scenario_id: UUID, body: ScenarioReviewInput, auth=authorized):
        from cutback.server.scenario_service import reviewed_content

        scope(auth, project_id, ("admin",))
        store = require_store(app)
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = found(session, project_id, scenario_id, lock=True)
            if row.version_sha256 != body.version_sha256:
                raise HTTPException(409, "SCENARIO_EXACT_CONTENT_VERSION_REQUIRED")
            try:
                reviewed_content(session, store, row)
            except ValueError:
                raise HTTPException(
                    409, "SCENARIO_COMPLETE_KNOWN_GENERATION_AND_CURRENT_RIGHTS_REQUIRED"
                ) from None
            decision = "APPROVED" if body.decision == "approve" else "REJECTED"
            if row.review_status == decision and row.reviewed_by == auth["actor"]:
                return public(session, row)
            if row.review_approval_id != body.expected_review_approval_id:
                raise HTTPException(409, "SCENARIO_REVIEW_CHANGED_RECONCILE_CURRENT_VERSION")
            previous = row.review_approval_id
            row.review_status = decision
            row.reviewed_by = auth["actor"]
            row.reviewed_at = datetime.now(UTC)
            row.review_approval_id = uuid4()
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="scenario_version_reviewed",
                    details={
                        "scenario_id": str(row.id),
                        "version_sha256": row.version_sha256,
                        "decision": decision,
                        "approval_ref": body.approval_ref,
                        "review_approval_id": str(row.review_approval_id),
                        "previous_review_approval_id": str(previous) if previous else None,
                    },
                )
            )
            return public(session, row)

    @app.post("/v1/projects/{project_id}/scenarios/{scenario_id}/cancel")
    def cancel(project_id: UUID, scenario_id: UUID, body: ScenarioCancelInput, auth=authorized):
        scope(auth, project_id, ("analyst",))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = found(session, project_id, scenario_id, lock=True)
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
                    run.results = {**run.results, "code": "SCENARIO_GENERATION_CANCELLED_NO_REPLAY"}
                    job.status = "CANCELLED"
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="scenario_generation_cancelled",
                    details={"scenario_id": str(row.id), "reason": body.reason},
                )
            )
            return public(session, row)
