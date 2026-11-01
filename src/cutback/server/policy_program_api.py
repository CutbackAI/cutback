"""Prepare one immutable common-policy search; HTTP never makes provider calls."""

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException, Query
from pydantic import UUID4, Field, JsonValue, field_validator, model_serializer, model_validator
from sqlalchemy import func, select

from cutback.core.improvement_policy import SourceImprovementPolicy
from cutback.core.input_routing import SOURCE_DATASET_CASE_BOUND, SOURCE_DATASET_CONTENT_BOUND
from cutback.core.models import Identifier, StrictModel
from cutback.core.policy_runtime_reference import PolicyRuntimeReference
from cutback.evals.campaigns import CampaignBudget, NativeGeneratorSpec
from cutback.evals.datasets import digest
from cutback.evals.learned_routing import RoutingFitOptions
from cutback.evals.source_policy_programs import SourcePolicyProgram, compile_policy_program
from cutback.policy_changes import SourcePolicyProposal
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
from cutback.server.policy_program_db import SourcePolicyProgramRecord, SourcePolicyProgramRun
from cutback.server.policy_program_service import approval_hash, load_program
from cutback.server.worker import load_dataset


class SourcePolicyProgramDraft(StrictModel):
    request_id: UUID4
    template_plan_id: UUID
    baseline_snapshot_id: UUID
    fit: RoutingFitOptions | None = Field(default_factory=RoutingFitOptions)
    generator: NativeGeneratorSpec | None = None
    budget: CampaignBudget
    conservative_savings_per_success_usd: Decimal | None = Field(
        default=None, ge=0, max_digits=24, decimal_places=12
    )
    forecast_successful_tasks: int | None = Field(default=None, strict=True, ge=1, le=1000000000)
    max_fraction_of_opportunity: Decimal = Field(default=Decimal("0.1"), gt=0, le=Decimal("0.25"))
    version: Literal["cutback.source-policy-program.v2"] | None = None
    runtime_reference: PolicyRuntimeReference | None = None

    @model_serializer(mode="wrap")
    def preserve_v1_request_identity(self, handler):
        value = handler(self)
        for key in ("version", "runtime_reference"):
            if getattr(self, key) is None:
                value.pop(key, None)
        return value

    @model_validator(mode="after")
    def runtime_version_pair(self):
        if (self.version is None) != (self.runtime_reference is None):
            raise ValueError("POLICY_PROGRAM_DISTINCT_RUNTIME_REFERENCE_VERSION_REQUIRED")
        return self

    @field_validator(
        "conservative_savings_per_success_usd", "max_fraction_of_opportunity", mode="before"
    )
    @classmethod
    def exact_money(cls, value):
        if value is not None and type(value) not in {str, int, Decimal}:
            raise ValueError("POLICY_PROGRAM_EXACT_DECIMAL_MONEY_REQUIRED")
        return value


class SourcePolicyProgramApproval(StrictModel):
    program_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    approval_ref: Identifier


class SourcePolicyProgramBrief(StrictModel):
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
    current_round: int
    known_cost_requests: int
    reserved_usd: str
    known_spend_subtotal_usd: str
    billing_complete: bool
    summary: dict[str, JsonValue]
    created_at: datetime
    expires_at: datetime
    deadline_at: datetime | None
    deployment_authorized: bool = False


class SourcePolicyProgramDetail(SourcePolicyProgramBrief):
    runs: list[dict[str, JsonValue]]


class SourcePolicyProgramPage(StrictModel):
    items: list[SourcePolicyProgramBrief]
    next_after: UUID | None


class SourcePolicyProgramContent(StrictModel):
    program_id: UUID
    program_hash: str
    specification: dict[str, JsonValue]
    policies: dict[str, SourceImprovementPolicy]
    deployment_authorized: bool = False


class SourcePolicyProgramCancel(StrictModel):
    pass


def brief(row):
    return SourcePolicyProgramBrief(
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
        current_round=row.current_round,
        known_cost_requests=row.known_cost_requests,
        reserved_usd=str(row.reserved_usd),
        known_spend_subtotal_usd=str(row.spent_usd),
        billing_complete=row.requests_used == row.known_cost_requests,
        summary=row.summary,
        created_at=row.created_at,
        expires_at=row.expires_at,
        deadline_at=row.deadline_at,
    )


def install_source_policy_programs(app, sessions, authorize, scope):
    authorized = Depends(authorize)

    def scoped(session, project, identifier, *, lock=False):
        row = session.get(SourcePolicyProgramRecord, identifier, with_for_update=lock)
        if not row or row.project_id != project:
            raise HTTPException(404, "POLICY_PROGRAM_NOT_FOUND")
        return row

    @app.post(
        "/v1/projects/{project_id}/source-policy-programs",
        status_code=201,
        response_model=SourcePolicyProgramBrief,
    )
    def prepare(project_id: UUID, body: SourcePolicyProgramDraft, auth=authorized):
        scope(auth, project_id, ("admin",))
        store = require_store(app)
        requested = body.model_dump(mode="json")
        try:
            with content_transaction(sessions, store, project_id) as (session, refs):
                lock_lineage(session, project_id)
                lock_preparation_identity(session, "source-policy-program", body.request_id)
                prior = session.scalar(
                    select(SourcePolicyProgramRecord).where(
                        SourcePolicyProgramRecord.project_id == project_id,
                        SourcePolicyProgramRecord.request_id == body.request_id,
                    )
                )
                if prior:
                    _, _, _, value = load_program(session, store, prior)
                    if prior.actor != auth["actor"] or value.get("requested") != requested:
                        raise HTTPException(409, "POLICY_PROGRAM_REQUEST_INTENT_CHANGED")
                    return brief(prior)
                if (
                    session.scalar(
                        select(func.count())
                        .select_from(SourcePolicyProgramRecord)
                        .where(SourcePolicyProgramRecord.project_id == project_id)
                    )
                    >= 1000
                ):
                    raise HTTPException(409, "POLICY_PROGRAM_PROJECT_QUOTA_EXHAUSTED")
                template = session.get(ExperimentPlanRecord, body.template_plan_id)
                if not template or template.project_id != project_id:
                    raise HTTPException(404, "POLICY_PROGRAM_TEMPLATE_NOT_FOUND")
                if any(
                    key in template.specification
                    for key in (
                        "source_policy_program_id",
                        "source_policy_program_sha256",
                        "routing_program_id",
                        "routing_program_sha256",
                        "campaign_id",
                        "campaign_sha256",
                    )
                ):
                    raise HTTPException(409, "POLICY_PROGRAM_REQUIRES_INDEPENDENT_TEMPLATE")
                plan = load_plan(store, project_id, template.specification)
                dataset = session.get(DatasetRecord, template.dataset_id)
                if not dataset or dataset.project_id != project_id:
                    raise HTTPException(404, "POLICY_PROGRAM_DATASET_NOT_FOUND")
                specification = SourcePolicyProgram.model_validate(
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
                    raise HTTPException(404, "POLICY_PROGRAM_ENVIRONMENT_NOT_FOUND")
                policy_id = UUID(dataset.provenance["policy_id"])
                _, policy = active_policy(session, project_id, policy_id)
                session.get(
                    CapturePolicyRecord, policy_id, with_for_update=True, populate_existing=True
                )
                _, policy = active_policy(session, project_id, policy_id)
                if not policy.permits(specification.environment, plan.workflow) or any(
                    p not in policy.allowed_providers for p in plan.approved_providers
                ):
                    raise HTTPException(403, "POLICY_PROGRAM_CONTENT_OR_VENDOR_POLICY_DENIED")
                from cutback.server.policy_service import validate_binding

                payloads = validate_binding(session, store, project_id, plan, dataset.id)
                from cutback.evals.policy_artifacts import decode_policy_bundle

                policies = decode_policy_bundle(payloads, plan.source_policy_execution)
                if specification.runtime_reference is not None:
                    from cutback.server.policy_runtime_reference import require_reference

                    execution = plan.source_policy_execution
                    require_reference(
                        session,
                        project_id,
                        environment,
                        execution.source,
                        policies[execution.reference.key],
                        specification.runtime_reference,
                    )
                loaded, _ = load_dataset(
                    session,
                    dataset,
                    store,
                    max_cases=SOURCE_DATASET_CASE_BOUND,
                    max_content_bytes=SOURCE_DATASET_CONTENT_BOUND,
                )
                snapshot = session.get(TaskCostSnapshot, body.baseline_snapshot_id)
                cost = snapshot_cost(snapshot, project_id, environment.id, plan.workflow)
                envelope = compile_policy_program(
                    specification,
                    loaded,
                    policies,
                    cost,
                    independent_labels=dataset.provenance.get("independent_labels"),
                )
                expiry = min(
                    policy.expires_at,
                    datetime.now(UTC) + timedelta(days=policy.retention_days),
                    datetime.fromisoformat(loaded["expires_at"]),
                    datetime.fromisoformat(template.specification["expires_at"]),
                )
                if specification.runtime_reference is not None:
                    expiry = min(expiry, specification.runtime_reference.expires_at)
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
                    raise HTTPException(409, "POLICY_PROGRAM_CONTENT_QUOTA_EXHAUSTED")
                ref = store.put(project_id, blob, policy.retention_days)
                refs.append(ref)
                sample = PayloadSample(
                    project_id=project_id,
                    policy_id=policy_id,
                    object_ref=ref,
                    content_hash=digest(clean),
                    expires_at=expiry,
                    metadata_safe={
                        "kind": "source_policy_program_envelope",
                        "source_policy_program_id": str(identifier),
                    },
                )
                session.add(sample)
                session.flush()
                row = SourcePolicyProgramRecord(
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
                        action="source_policy_program_prepared",
                        details={"program_id": str(row.id), "eligible": envelope["eligible"]},
                    )
                )
                session.flush()
                from cutback.server.policy_program_service import link_payload

                execution = plan.source_policy_execution
                if execution is None:
                    raise ValueError("POLICY_PROGRAM_SOURCE_EXECUTION_REQUIRED")
                for reference in [execution.reference, *execution.candidates]:
                    link_payload(
                        session,
                        row,
                        session.get(PayloadSample, reference.payload_id),
                        "reference" if reference == execution.reference else "static",
                        derivation={"reference": reference.model_dump(mode="json")},
                    )
                return brief(row)
        except ValueError as exc:
            code = str(exc)
            raise HTTPException(
                409,
                code
                if re.fullmatch(r"[A-Z_]{3,110}", code)
                else "POLICY_PROGRAM_TEMPLATE_ECONOMICS_OR_CONTENT_INVALID",
            ) from None

    @app.get(
        "/v1/projects/{project_id}/source-policy-programs", response_model=SourcePolicyProgramPage
    )
    def listing(
        project_id: UUID,
        after: UUID | None = None,
        limit: int = Query(20, ge=1, le=50),
        auth=authorized,
    ):
        scope(auth, project_id)
        with sessions() as session:
            query = select(SourcePolicyProgramRecord).where(
                SourcePolicyProgramRecord.project_id == project_id
            )
            if after:
                query = query.where(SourcePolicyProgramRecord.id > after)
            rows = session.scalars(
                query.order_by(SourcePolicyProgramRecord.id).limit(limit + 1)
            ).all()
            return SourcePolicyProgramPage(
                items=[brief(row) for row in rows[:limit]],
                next_after=rows[limit - 1].id if len(rows) > limit else None,
            )

    @app.get(
        "/v1/projects/{project_id}/source-policy-programs/{program_id}",
        response_model=SourcePolicyProgramDetail,
    )
    def detail(project_id: UUID, program_id: UUID, auth=authorized):
        scope(auth, project_id)
        with sessions() as session:
            row = scoped(session, project_id, program_id)
            pairs = session.execute(
                select(SourcePolicyProgramRun, ExperimentRunRecord)
                .join(ExperimentRunRecord, SourcePolicyProgramRun.run_id == ExperimentRunRecord.id)
                .where(SourcePolicyProgramRun.program_id == row.id)
                .order_by(SourcePolicyProgramRun.created_at, SourcePolicyProgramRun.id)
            ).all()
            return SourcePolicyProgramDetail(
                **brief(row).model_dump(),
                runs=[
                    {
                        "id": str(run.id),
                        "phase": link.phase,
                        "round_index": link.round_index,
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
        "/v1/projects/{project_id}/source-policy-programs/{program_id}/approve",
        response_model=SourcePolicyProgramBrief,
    )
    def approve(
        project_id: UUID, program_id: UUID, body: SourcePolicyProgramApproval, auth=authorized
    ):
        scope(auth, project_id, ("admin",))
        try:
            with sessions.begin() as session:
                lock_lineage(session, project_id)
                row = scoped(session, project_id, program_id, lock=True)
                if body.program_hash != row.approval_sha256:
                    raise HTTPException(409, "POLICY_PROGRAM_HASH_CHANGED")
                if row.approval_status != "pending" or row.status != "READY_FOR_APPROVAL":
                    raise HTTPException(409, "POLICY_PROGRAM_ALREADY_DECIDED_OR_INELIGIBLE")
                specification, _, _, _ = load_program(session, require_store(app), row)
                if row.economic_envelope.get("eligible") is not True:
                    raise HTTPException(409, "POLICY_PROGRAM_INELIGIBLE")
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
                        action="source_policy_program_approved",
                        details={
                            "program_id": str(row.id),
                            "program_hash": body.program_hash,
                            "approval_ref": body.approval_ref,
                        },
                    )
                )
                return brief(row)
        except ValueError:
            raise HTTPException(409, "POLICY_PROGRAM_CURRENT_AUTHORITY_OR_INPUTS_INVALID") from None

    @app.post(
        "/v1/projects/{project_id}/source-policy-programs/{program_id}/cancel",
        response_model=SourcePolicyProgramBrief,
    )
    def cancel(
        project_id: UUID, program_id: UUID, body: SourcePolicyProgramCancel, auth=authorized
    ):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = scoped(session, project_id, program_id, lock=True)
            if row.status == "CANCELLED":
                return brief(row)
            if row.status in {"COMPLETED", "FAILED", "EXPIRED", "ABSTAINED"}:
                raise HTTPException(409, "POLICY_PROGRAM_TERMINAL")
            row.status, row.completed_at = "CANCELLED", datetime.now(UTC)
            row.lease_token = row.lease_until = None
            row.summary = {
                "code": "cancelled_by_owner",
                "deployment_authorized": False,
                "unsettled_reservations": row.requests_used - row.known_cost_requests,
            }
            for run in session.scalars(
                select(ExperimentRunRecord)
                .join(
                    SourcePolicyProgramRun, SourcePolicyProgramRun.run_id == ExperimentRunRecord.id
                )
                .where(SourcePolicyProgramRun.program_id == row.id)
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
                    action="source_policy_program_cancelled",
                    details={"program_id": str(row.id)},
                )
            )
            return brief(row)

    @app.post(
        "/v1/projects/{project_id}/source-policy-programs/{program_id}/content",
        response_model=SourcePolicyProgramContent,
    )
    def content(project_id: UUID, program_id: UUID, body: ContentPurpose, auth=authorized):
        from cutback.server.policy_program_worker import all_policies

        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = scoped(session, project_id, program_id)
            try:
                specification, _, _, _ = load_program(session, require_store(app), row)
                _, policies = all_policies(
                    session,
                    require_store(app),
                    row,
                    specification,
                    kinds={"static", "generated", "learned"},
                )
            except ValueError:
                raise HTTPException(409, "POLICY_PROGRAM_CURRENT_CONTENT_UNAVAILABLE") from None
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="source_policy_program_content_read",
                    details={"program_id": str(row.id), "purpose": body.purpose},
                )
            )
            return {
                "program_id": row.id,
                "program_hash": row.approval_sha256,
                "specification": specification.model_dump(mode="json"),
                "policies": {key: value.model_dump(mode="json") for key, value in policies.items()},
                "deployment_authorized": False,
            }

    @app.post(
        "/v1/projects/{project_id}/source-policy-programs/{program_id}/source-proposal",
        response_model=SourcePolicyProposal,
    )
    def proposal(project_id: UUID, program_id: UUID, body: ContentPurpose, auth=authorized):
        from cutback.server.policy_program_service import source_proposal

        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = scoped(session, project_id, program_id)
            try:
                result = source_proposal(session, require_store(app), row)
            except ValueError:
                raise HTTPException(
                    409, "POLICY_PROGRAM_CURRENT_CONFIRMED_SOURCE_REQUIRED"
                ) from None
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="source_policy_program_source_proposal_read",
                    details={
                        "program_id": str(row.id),
                        "run_id": str(result.run_id),
                        "purpose": body.purpose,
                    },
                )
            )
            return result
