"""Prepare and approve one immutable routing budget; HTTP never makes native calls."""

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException, Query
from pydantic import UUID4, Field, JsonValue
from sqlalchemy import func, select

from cutback.core.input_routing import SOURCE_DATASET_CASE_BOUND, SOURCE_DATASET_CONTENT_BOUND
from cutback.core.models import Identifier, StrictModel
from cutback.evals.campaigns import CampaignBudget
from cutback.evals.datasets import digest
from cutback.evals.learned_routing import RoutingFitOptions
from cutback.evals.routing_programs import RoutingProgram, compile_routing_program
from cutback.routing_changes import RoutingSourceProposal
from cutback.server.campaign_content import content_transaction
from cutback.server.campaign_service import snapshot_cost
from cutback.server.content_policy import policy_content
from cutback.server.dataset_lineage import lock_lineage, lock_preparation_identity
from cutback.server.db import (
    AuditEvent,
    CapturePolicyRecord,
    DatasetRecord,
    Environment,
    ExperimentPlanRecord,
    ExperimentRunRecord,
    JobRecord,
    PayloadSample,
)
from cutback.server.economics_db import TaskCostSnapshot
from cutback.server.evaluation_api import ContentPurpose, active_policy, require_store
from cutback.server.plan_artifacts import load_plan, public_config
from cutback.server.routing_program_db import RoutingProgramRecord, RoutingProgramRun
from cutback.server.routing_program_service import approval_hash, load_program
from cutback.server.worker import load_dataset


class RoutingProgramDraft(StrictModel):
    request_id: UUID4
    template_plan_id: UUID
    baseline_snapshot_id: UUID
    fit: RoutingFitOptions = Field(default_factory=RoutingFitOptions)
    budget: CampaignBudget
    conservative_savings_per_success_usd: Decimal | None = Field(
        default=None, ge=0, max_digits=24, decimal_places=12
    )
    forecast_successful_tasks: int | None = Field(default=None, strict=True, ge=1, le=1000000000)
    max_fraction_of_opportunity: Decimal = Field(default=Decimal("0.1"), gt=0, le=Decimal("0.25"))


class RoutingProgramApproval(StrictModel):
    program_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    approval_ref: Identifier


class RoutingProgramBrief(StrictModel):
    id: UUID
    environment_id: UUID
    template_plan_id: UUID
    dataset_id: UUID
    program_hash: str
    status: str
    approval_status: str
    specification: dict[str, JsonValue]
    economic_envelope: dict[str, JsonValue]
    requests: int
    known_cost_requests: int
    reserved_usd: str
    known_spend_subtotal_usd: str
    billing_complete: bool
    summary: dict[str, JsonValue]
    created_at: datetime
    expires_at: datetime
    deadline_at: datetime | None
    deployment_authorized: bool = False


class RoutingProgramDetail(RoutingProgramBrief):
    runs: list[dict[str, JsonValue]]


class RoutingProgramPage(StrictModel):
    items: list[RoutingProgramBrief]
    next_after: UUID | None


class RoutingProgramCancel(StrictModel):
    pass


def brief(row):
    return RoutingProgramBrief(
        id=row.id,
        environment_id=row.environment_id,
        template_plan_id=row.template_plan_id,
        dataset_id=row.dataset_id,
        program_hash=row.approval_sha256,
        status=row.status,
        approval_status=row.approval_status,
        specification=row.specification,
        economic_envelope=row.economic_envelope,
        requests=row.requests_used,
        known_cost_requests=row.known_cost_requests,
        reserved_usd=str(row.reserved_usd),
        known_spend_subtotal_usd=str(row.spent_usd),
        billing_complete=row.requests_used == row.known_cost_requests,
        summary=row.summary,
        created_at=row.created_at,
        expires_at=row.expires_at,
        deadline_at=row.deadline_at,
    )


def install_routing_programs(app, sessions, authorize, scope):
    authorized = Depends(authorize)

    def scoped(session, project, identifier, *, lock=False):
        row = session.get(RoutingProgramRecord, identifier, with_for_update=lock)
        if not row or row.project_id != project:
            raise HTTPException(404, "ROUTING_PROGRAM_NOT_FOUND")
        return row

    @app.post(
        "/v1/projects/{project_id}/routing/programs",
        status_code=201,
        response_model=RoutingProgramBrief,
    )
    def prepare(project_id: UUID, body: RoutingProgramDraft, auth=authorized):
        scope(auth, project_id, ("admin",))
        store = require_store(app)
        requested = body.model_dump(mode="json")
        try:
            with content_transaction(sessions, store, project_id) as (session, refs):
                lock_lineage(session, project_id)
                lock_preparation_identity(session, "routing-program", body.request_id)
                prior = session.scalar(
                    select(RoutingProgramRecord).where(
                        RoutingProgramRecord.project_id == project_id,
                        RoutingProgramRecord.request_id == body.request_id,
                    )
                )
                if prior:
                    _, _, _, value = load_program(session, store, prior)
                    if prior.actor != auth["actor"] or value.get("requested") != requested:
                        raise HTTPException(409, "ROUTING_PROGRAM_REQUEST_INTENT_CHANGED")
                    return brief(prior)
                if (
                    session.scalar(
                        select(func.count())
                        .select_from(RoutingProgramRecord)
                        .where(RoutingProgramRecord.project_id == project_id)
                    )
                    >= 1000
                ):
                    raise HTTPException(409, "ROUTING_PROGRAM_PROJECT_QUOTA_EXHAUSTED")
                template = session.get(ExperimentPlanRecord, body.template_plan_id)
                if not template or template.project_id != project_id:
                    raise HTTPException(404, "ROUTING_PROGRAM_TEMPLATE_NOT_FOUND")
                if template.specification.get("routing_program_id"):
                    raise HTTPException(409, "ROUTING_PROGRAM_REQUIRES_INDEPENDENT_TEMPLATE")
                plan = load_plan(store, project_id, template.specification)
                dataset = session.get(DatasetRecord, template.dataset_id)
                if not dataset or dataset.project_id != project_id:
                    raise HTTPException(404, "ROUTING_PROGRAM_DATASET_NOT_FOUND")
                specification = RoutingProgram.model_validate(
                    {
                        "dataset_id": str(dataset.id),
                        "baseline_snapshot_id": str(body.baseline_snapshot_id),
                        "environment": dataset.provenance["environment"],
                        "template": plan.model_dump(mode="json"),
                        **{
                            key: value
                            for key, value in requested.items()
                            if key not in {"request_id", "template_plan_id", "baseline_snapshot_id"}
                        },
                    }
                )
                environment = session.scalar(
                    select(Environment).where(
                        Environment.project_id == project_id,
                        Environment.name == specification.environment,
                    )
                )
                if not environment:
                    raise HTTPException(404, "ROUTING_PROGRAM_ENVIRONMENT_NOT_FOUND")
                policy_id = UUID(dataset.provenance["policy_id"])
                _, policy = active_policy(session, project_id, policy_id)
                session.get(
                    CapturePolicyRecord, policy_id, with_for_update=True, populate_existing=True
                )
                _, policy = active_policy(session, project_id, policy_id)
                if not policy.permits(specification.environment, plan.workflow) or any(
                    p not in policy.allowed_providers for p in plan.approved_providers
                ):
                    raise HTTPException(403, "ROUTING_PROGRAM_CONTENT_OR_VENDOR_POLICY_DENIED")
                from cutback.server.routing_service import validate_binding

                validate_binding(session, store, project_id, plan, dataset_id=dataset.id)
                loaded, _ = load_dataset(
                    session,
                    dataset,
                    store,
                    max_cases=SOURCE_DATASET_CASE_BOUND,
                    max_content_bytes=SOURCE_DATASET_CONTENT_BOUND,
                )
                snapshot = session.get(TaskCostSnapshot, body.baseline_snapshot_id)
                cost = snapshot_cost(snapshot, project_id, environment.id, plan.workflow)
                envelope = compile_routing_program(
                    specification,
                    loaded,
                    cost,
                    independent_labels=dataset.provenance.get("independent_labels"),
                )
                expiry = min(
                    policy.expires_at,
                    datetime.now(UTC) + timedelta(days=policy.retention_days),
                    datetime.fromisoformat(loaded["expires_at"]),
                    datetime.fromisoformat(template.specification["expires_at"]),
                )
                identifier = uuid4()
                clean, blob = policy_content(
                    {
                        "content_nonce": str(uuid4()),
                        "requested": requested,
                        "specification": specification.model_dump(mode="json"),
                    },
                    policy,
                    preserve_semantics=True,
                )
                from cutback.server.evaluation_api import policy_object_count

                if policy_object_count(session, policy_id) >= policy.max_samples:
                    raise HTTPException(409, "ROUTING_PROGRAM_CONTENT_QUOTA_EXHAUSTED")
                ref = store.put(project_id, blob, policy.retention_days)
                refs.append(ref)
                sample = PayloadSample(
                    project_id=project_id,
                    policy_id=policy_id,
                    object_ref=ref,
                    content_hash=digest(clean),
                    expires_at=expiry,
                    metadata_safe={
                        "kind": "routing_program_envelope",
                        "routing_program_id": str(identifier),
                    },
                )
                session.add(sample)
                session.flush()
                row = RoutingProgramRecord(
                    id=identifier,
                    project_id=project_id,
                    environment_id=environment.id,
                    request_id=body.request_id,
                    dataset_id=dataset.id,
                    policy_id=policy_id,
                    template_plan_id=template.id,
                    template_plan_sha256=template.specification["plan_hash"],
                    snapshot_id=snapshot.id,
                    snapshot_sha256=snapshot.evidence_sha256,
                    payload_id=sample.id,
                    request_sha256=sample.content_hash,
                    approval_sha256="0" * 64,
                    specification=public_config(
                        specification.model_dump(mode="json"), redact_content_hashes=True
                    ),
                    economic_envelope=envelope,
                    expires_at=expiry,
                    actor=auth["actor"],
                    status="READY_FOR_APPROVAL" if envelope["eligible"] else "ABSTAINED",
                    summary={
                        "code": "approval_required"
                        if envelope["eligible"]
                        else "economic_opportunity_or_evidence_insufficient",
                        "deployment_authorized": False,
                    },
                )
                row.approval_sha256 = approval_hash(row, sample.content_hash)
                session.add(row)
                session.add(
                    AuditEvent(
                        project_id=project_id,
                        actor=auth["actor"],
                        action="routing_program_prepared",
                        details={"program_id": str(row.id), "eligible": envelope["eligible"]},
                    )
                )
                session.flush()
                return brief(row)
        except ValueError as exc:
            code = str(exc)
            raise HTTPException(
                409,
                code
                if re.fullmatch(r"[A-Z_]{3,110}", code)
                else "ROUTING_PROGRAM_TEMPLATE_ECONOMICS_OR_CONTENT_INVALID",
            ) from None

    @app.get("/v1/projects/{project_id}/routing/programs", response_model=RoutingProgramPage)
    def listing(
        project_id: UUID,
        after: UUID | None = None,
        limit: int = Query(20, ge=1, le=50),
        auth=authorized,
    ):
        scope(auth, project_id)
        with sessions() as session:
            query = select(RoutingProgramRecord).where(
                RoutingProgramRecord.project_id == project_id
            )
            if after:
                query = query.where(RoutingProgramRecord.id > after)
            rows = session.scalars(query.order_by(RoutingProgramRecord.id).limit(limit + 1)).all()
            return RoutingProgramPage(
                items=[brief(row) for row in rows[:limit]],
                next_after=rows[limit - 1].id if len(rows) > limit else None,
            )

    @app.get(
        "/v1/projects/{project_id}/routing/programs/{program_id}",
        response_model=RoutingProgramDetail,
    )
    def detail(project_id: UUID, program_id: UUID, auth=authorized):
        scope(auth, project_id)
        with sessions() as session:
            row = scoped(session, project_id, program_id)
            pairs = session.execute(
                select(RoutingProgramRun, ExperimentRunRecord)
                .join(ExperimentRunRecord, RoutingProgramRun.run_id == ExperimentRunRecord.id)
                .where(RoutingProgramRun.program_id == row.id)
                .order_by(RoutingProgramRun.created_at, RoutingProgramRun.id)
            ).all()
            return RoutingProgramDetail(
                **brief(row).model_dump(),
                runs=[
                    {
                        "id": str(run.id),
                        "phase": link.phase,
                        "status": run.status,
                        "plan_hash": link.plan_sha256,
                        "requests": run.requests_used,
                        "known_cost_requests": run.known_cost_requests,
                        "reserved_usd": str(run.reserved_usd),
                        "known_spend_subtotal_usd": str(run.spent_usd),
                    }
                    for link, run in pairs
                ],
            )

    @app.post(
        "/v1/projects/{project_id}/routing/programs/{program_id}/approve",
        response_model=RoutingProgramBrief,
    )
    def approve(project_id: UUID, program_id: UUID, body: RoutingProgramApproval, auth=authorized):
        scope(auth, project_id, ("admin",))
        try:
            with sessions.begin() as session:
                lock_lineage(session, project_id)
                row = scoped(session, project_id, program_id, lock=True)
                if body.program_hash != row.approval_sha256:
                    raise HTTPException(409, "ROUTING_PROGRAM_HASH_CHANGED")
                if row.approval_status != "pending" or row.status != "READY_FOR_APPROVAL":
                    raise HTTPException(409, "ROUTING_PROGRAM_ALREADY_DECIDED_OR_INELIGIBLE")
                specification, _, _, _ = load_program(session, require_store(app), row)
                if row.economic_envelope.get("eligible") is not True:
                    raise HTTPException(409, "ROUTING_PROGRAM_INELIGIBLE")
                now = datetime.now(UTC)
                row.approval_status, row.approved_by, row.status = (
                    "consumed",
                    auth["actor"],
                    "QUEUED",
                )
                row.started_at, row.available_at = now, now
                row.deadline_at = min(
                    row.expires_at, now + timedelta(seconds=specification.budget.max_wall_seconds)
                )
                row.summary = {
                    "code": "approved_for_bounded_execution",
                    "deployment_authorized": False,
                }
                session.add(
                    AuditEvent(
                        project_id=project_id,
                        actor=auth["actor"],
                        action="routing_program_approved",
                        details={
                            "program_id": str(row.id),
                            "program_hash": body.program_hash,
                            "approval_ref": body.approval_ref,
                        },
                    )
                )
                return brief(row)
        except ValueError:
            raise HTTPException(
                409, "ROUTING_PROGRAM_CURRENT_AUTHORITY_OR_INPUTS_INVALID"
            ) from None

    @app.post(
        "/v1/projects/{project_id}/routing/programs/{program_id}/cancel",
        response_model=RoutingProgramBrief,
    )
    def cancel(project_id: UUID, program_id: UUID, body: RoutingProgramCancel, auth=authorized):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = scoped(session, project_id, program_id, lock=True)
            if row.status == "CANCELLED":
                return brief(row)
            if row.status in {"COMPLETED", "FAILED", "EXPIRED", "ABSTAINED"}:
                raise HTTPException(409, "ROUTING_PROGRAM_TERMINAL")
            row.status, row.completed_at = "CANCELLED", datetime.now(UTC)
            row.lease_token = row.lease_until = None
            row.summary = {
                "code": "cancelled_by_owner",
                "deployment_authorized": False,
                "unsettled_reservations": row.requests_used - row.known_cost_requests,
            }
            for run in session.scalars(
                select(ExperimentRunRecord)
                .join(RoutingProgramRun, RoutingProgramRun.run_id == ExperimentRunRecord.id)
                .where(RoutingProgramRun.program_id == row.id)
                .with_for_update(of=ExperimentRunRecord)
            ):
                if run.status in {"QUEUED", "RUNNING", "BLOCKED"}:
                    run.status = "CANCELLED"
                    job = session.scalar(
                        select(JobRecord).where(JobRecord.run_id == run.id).with_for_update()
                    )
                    if job:
                        job.status = "CANCELLED"
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="routing_program_cancelled",
                    details={"program_id": str(row.id)},
                )
            )
            return brief(row)

    @app.post("/v1/projects/{project_id}/routing/programs/{program_id}/content")
    def content(project_id: UUID, program_id: UUID, body: ContentPurpose, auth=authorized):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = scoped(session, project_id, program_id)
            try:
                specification, _, _, _ = load_program(session, require_store(app), row)
            except ValueError:
                raise HTTPException(409, "ROUTING_PROGRAM_CURRENT_CONTENT_UNAVAILABLE") from None
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="routing_program_content_read",
                    details={"program_id": str(row.id), "purpose": body.purpose},
                )
            )
            return {
                "program_id": row.id,
                "program_hash": row.approval_sha256,
                "specification": specification.model_dump(mode="json"),
                "deployment_authorized": False,
            }

    @app.post(
        "/v1/projects/{project_id}/routing/programs/{program_id}/source-proposal",
        response_model=RoutingSourceProposal,
    )
    def proposal(project_id: UUID, program_id: UUID, body: ContentPurpose, auth=authorized):
        from cutback.server.routing_program_service import source_proposal

        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = scoped(session, project_id, program_id)
            try:
                result = source_proposal(session, require_store(app), row)
            except ValueError:
                raise HTTPException(
                    409, "ROUTING_PROGRAM_CURRENT_CONFIRMED_SOURCE_REQUIRED"
                ) from None
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="routing_program_source_proposal_read",
                    details={
                        "program_id": str(row.id),
                        "run_id": str(result.run_id),
                        "purpose": body.purpose,
                    },
                )
            )
            return result
