"""Explicit bounded campaign preparation, approval and cancellation; no inference in HTTP."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException, Query
from pydantic import Field, JsonValue
from sqlalchemy import func, select

from cutback.core.instruction_edits import LiteralInstructionEdit
from cutback.core.models import Identifier, StrictModel
from cutback.evals.campaign_budget import compile_campaign
from cutback.evals.campaigns import (
    CampaignBudget,
    CampaignVersion,
    ImprovementCampaign,
    automatic_static_portfolio,
    generation_input,
)
from cutback.evals.experiments import ExperimentPlan, ModelSpec, upper_cost
from cutback.server.campaign_content import (
    content_transaction,
    current_policy,
    put_payload,
    read_payload,
)
from cutback.server.campaign_db import (
    CampaignCandidate,
    CampaignEconomicReference,
    CampaignRecord,
    CampaignRun,
    CampaignSelection,
)
from cutback.server.campaign_service import (
    approval_hash,
    cached_instruction_edits,
    candidate_model,
    load_campaign,
    require_source_registered,
    snapshot_cost,
)
from cutback.server.dataset_lineage import lock_lineage
from cutback.server.db import (
    AuditEvent,
    DatasetRecord,
    Environment,
    ExperimentPlanRecord,
    ExperimentRunRecord,
    JobRecord,
    RecommendationRecord,
)
from cutback.server.economics_db import TaskCostSnapshot
from cutback.server.evaluation_api import ContentPurpose, active_policy, require_store
from cutback.server.plan_artifacts import load_plan, public_config
from cutback.server.worker import load_dataset


class CampaignInput(StrictModel):
    request_id: UUID
    specification: ImprovementCampaign


class CampaignApproval(StrictModel):
    campaign_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    approval_ref: Identifier


class CampaignCancel(StrictModel):
    pass


class CampaignCandidateContent(StrictModel):
    id: UUID
    configuration_sha256: str
    configuration: ModelSpec


class CampaignSelectedContent(StrictModel):
    campaign_id: UUID
    campaign_hash: str
    run_id: UUID
    plan_id: UUID
    plan_hash: str
    plan: ExperimentPlan
    recommendation_id: UUID
    recommendation_status: str
    evidence: dict[str, JsonValue]
    instruction_edit: LiteralInstructionEdit | None
    max_instruction_bytes: int
    deployment_authorized: bool = False


class CampaignDraft(StrictModel):
    version: CampaignVersion = "cutback.improvement-campaign.v1"
    request_id: UUID
    template_plan_id: UUID
    baseline_snapshot_id: UUID
    generator_model: ModelSpec
    budget: CampaignBudget
    rounds: int = Field(default=1, strict=True, ge=1, le=3)
    variants_per_round: int = Field(default=2, strict=True, ge=1, le=4)
    instruction_edit: LiteralInstructionEdit | None = None
    conservative_savings_per_success_usd: str | None = Field(default=None, max_length=30)
    forecast_successful_tasks: int | None = Field(default=None, strict=True, ge=1, le=1000000000)


class CampaignBrief(StrictModel):
    id: UUID
    environment_id: UUID
    status: str
    approval_status: str
    campaign_hash: str
    workflow: str
    workflow_label: str
    environment: str
    generator_model: str
    approved_providers: list[str]
    max_wall_seconds: int
    max_instruction_bytes: int
    source_profile_sha256: str | None
    instruction_edit_qualified: bool
    specification: dict[str, JsonValue]
    economic_envelope: dict[str, JsonValue]
    current_round: int
    requests: int
    known_cost_requests: int
    reserved_usd: str
    known_spend_subtotal_usd: str
    billing_complete: bool
    summary: dict[str, JsonValue]
    created_at: datetime
    expires_at: datetime


class CampaignDetail(CampaignBrief):
    runs: list[dict[str, JsonValue]]
    candidates: list[dict[str, JsonValue]]


class CampaignPage(StrictModel):
    items: list[CampaignBrief]
    next_after: UUID | None


class CampaignTemplate(StrictModel):
    id: UUID
    dataset_id: UUID
    workflow: str
    workflow_label: str
    baseline_provider: str
    baseline_model: str
    source_bound: bool
    instruction_edit: LiteralInstructionEdit | None = None


class CampaignTemplatePage(StrictModel):
    items: list[CampaignTemplate]
    next_after: UUID | None


def brief(row):
    return CampaignBrief(
        id=row.id,
        environment_id=row.environment_id,
        status=row.status,
        approval_status=row.approval_status,
        campaign_hash=row.approval_sha256,
        workflow=row.specification["template"]["workflow"],
        workflow_label=row.specification["template"].get("name")
        or row.specification["template"]["workflow"],
        environment=row.specification["environment"],
        generator_model=row.specification["generator"]["model"]["model"],
        approved_providers=row.specification["template"]["approved_providers"],
        max_wall_seconds=row.specification["budget"]["max_wall_seconds"],
        max_instruction_bytes=row.specification["generator"]["max_instruction_bytes"],
        source_profile_sha256=(row.specification["template"].get("source_binding") or {}).get(
            "profile_sha256"
        ),
        instruction_edit_qualified=bool(row.specification.get("instruction_edit")),
        specification=row.specification,
        economic_envelope=row.economic_envelope,
        current_round=row.current_round,
        requests=row.requests_used,
        known_cost_requests=row.known_cost_requests,
        reserved_usd=str(row.reserved_usd),
        known_spend_subtotal_usd=str(row.spent_usd),
        billing_complete=row.requests_used == row.known_cost_requests,
        summary=row.summary,
        created_at=row.created_at,
        expires_at=row.expires_at,
    )


def training_lineage(session, project, dataset):
    from cutback.server.lineage_db import DatasetFamily

    identifiers = {
        UUID(i["lineage_group_id"]) for i in dataset["items"] if i.get("lineage_group_id")
    }
    canonical = dict(
        session.execute(
            select(DatasetFamily.id, DatasetFamily.canonical_id).where(
                DatasetFamily.project_id == project, DatasetFamily.id.in_(identifiers)
            )
        ).all()
    )
    for item in dataset["items"]:
        item["canonical_family_id"] = (
            canonical.get(UUID(item["lineage_group_id"])) if item.get("lineage_group_id") else None
        )
    return dataset


def install_campaigns(app, sessions, authorize, scope):
    def scoped(session, project, identifier, *, lock=False):
        row = session.get(CampaignRecord, identifier, with_for_update=lock)
        if not row or row.project_id != project:
            raise HTTPException(404, "CAMPAIGN_NOT_FOUND")
        return row

    @app.get("/v1/projects/{project_id}/campaign-templates", response_model=CampaignTemplatePage)
    def templates(
        project_id: UUID,
        after: UUID | None = None,
        limit: int = Query(20, ge=1, le=50),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            query = select(ExperimentPlanRecord).where(
                ExperimentPlanRecord.project_id == project_id,
                ExperimentPlanRecord.specification["public_specification"]["baseline"]["provider"]
                .as_string()
                .in_(["openai", "anthropic"]),
                ExperimentPlanRecord.specification["public_specification"]["staged"]
                .as_boolean()
                .is_(False),
                ExperimentPlanRecord.specification["public_specification"]["operation"]
                .as_string()
                .is_(None),
                ~select(CampaignRun.id)
                .join(ExperimentRunRecord, ExperimentRunRecord.id == CampaignRun.run_id)
                .where(ExperimentRunRecord.plan_id == ExperimentPlanRecord.id)
                .exists(),
            )
            if after:
                query = query.where(ExperimentPlanRecord.id > after)
            rows = session.scalars(query.order_by(ExperimentPlanRecord.id).limit(limit + 1)).all()
            from cutback.server.workflow_labels import workflow_labels

            labels = workflow_labels(session, project_id) if rows else {}
            edits = cached_instruction_edits(
                session,
                project_id,
                {
                    (r.specification["public_specification"].get("source_binding") or {}).get(
                        "profile_sha256"
                    )
                    for r in rows[:limit]
                }
                - {None},
            )
            return CampaignTemplatePage(
                items=[
                    CampaignTemplate(
                        id=r.id,
                        dataset_id=r.dataset_id,
                        workflow=r.specification["public_specification"]["workflow"],
                        workflow_label=labels.get(
                            r.specification["public_specification"]["workflow"]
                        )
                        or r.specification["public_specification"].get("name")
                        or r.specification["public_specification"]["workflow"],
                        baseline_provider=r.specification["public_specification"]["baseline"][
                            "provider"
                        ],
                        baseline_model=r.specification["public_specification"]["baseline"]["model"],
                        source_bound=bool(
                            r.specification["public_specification"].get("source_binding")
                        ),
                        instruction_edit=edits.get(
                            (
                                r.specification["public_specification"].get("source_binding") or {}
                            ).get("profile_sha256")
                        ),
                    )
                    for r in rows[:limit]
                ],
                next_after=rows[limit - 1].id if len(rows) > limit else None,
            )

    @app.post("/v1/projects/{project_id}/campaigns", status_code=201, response_model=CampaignBrief)
    def prepare(project_id: UUID, body: CampaignInput, auth=Depends(authorize)):
        scope(auth, project_id, ("analyst",))
        if not body.request_id.int:
            raise HTTPException(422, "NONZERO_CAMPAIGN_REQUEST_ID_REQUIRED")
        store = require_store(app)
        requested = body.specification.model_dump(mode="json")
        try:
            with content_transaction(sessions, store, project_id) as (session, refs):
                lock_lineage(session, project_id)
                existing = session.scalar(
                    select(CampaignRecord).where(
                        CampaignRecord.project_id == project_id,
                        CampaignRecord.request_id == body.request_id,
                    )
                )
                if existing:
                    _, policy = active_policy(session, project_id, existing.policy_id)
                    content, _ = read_payload(session, store, existing, existing.payload_id, policy)
                    if content["requested"] != requested:
                        raise HTTPException(409, "CAMPAIGN_REQUEST_ID_CONFLICT")
                    return brief(existing)
                if (
                    session.scalar(
                        select(func.count())
                        .select_from(CampaignRecord)
                        .where(CampaignRecord.project_id == project_id)
                    )
                    >= 1000
                ):
                    raise HTTPException(429, "CAMPAIGN_PROJECT_QUOTA_EXHAUSTED")
                campaign = body.specification
                dataset = session.get(DatasetRecord, campaign.dataset_id)
                if (
                    not dataset
                    or dataset.project_id != project_id
                    or dataset.workflow_id != campaign.template.workflow
                    or dataset.provenance["environment"] != campaign.environment
                ):
                    raise HTTPException(404, "CAMPAIGN_DATASET_NOT_FOUND")
                env = session.scalar(
                    select(Environment).where(
                        Environment.project_id == project_id,
                        Environment.name == campaign.environment,
                    )
                )
                if env is None:
                    raise HTTPException(404, "CAMPAIGN_ENVIRONMENT_NOT_FOUND")
                policy_id = UUID(dataset.provenance["policy_id"])
                _, policy = active_policy(session, project_id, policy_id)
                if any(
                    p not in policy.allowed_providers for p in campaign.template.approved_providers
                ):
                    raise HTTPException(403, "CAMPAIGN_VENDOR_POLICY_DENIED")
                require_source_registered(session, project_id, campaign.template)
                loaded, _ = load_dataset(session, dataset, store)
                training_lineage(session, project_id, loaded)
                if campaign.version != "cutback.improvement-campaign.v3":
                    generation_input(campaign, loaded["items"])
                snapshot = session.get(TaskCostSnapshot, campaign.baseline_snapshot_id)
                cost = snapshot_cost(snapshot, project_id, env.id, campaign.template.workflow)
                envelope = compile_campaign(campaign, loaded, cost)
                envelope["baseline_snapshot_sha256"] = snapshot.evidence_sha256
                if envelope["eligible"]:
                    raw = campaign.model_dump(mode="json")
                    if campaign.version == "cutback.improvement-campaign.v3":
                        raw["static_portfolio"] = [
                            m.model_dump(mode="json")
                            for m in campaign.static_portfolio or []
                            if m.key in envelope["selected_static_keys"]
                        ]
                    if envelope["rounds"]:
                        raw["generator"].update(
                            rounds=envelope["rounds"],
                            variants_per_round=envelope["variants_per_round"],
                        )
                    campaign = ImprovementCampaign.model_validate(raw)
                    if campaign.version == "cutback.improvement-campaign.v3" and envelope["rounds"]:
                        generation_input(campaign, loaded["items"])
                identity = uuid4()
                expiry = min(
                    policy.expires_at,
                    datetime.now(UTC) + timedelta(days=policy.retention_days),
                    datetime.fromisoformat(loaded["expires_at"]),
                )
                context = SimpleNamespace(
                    id=identity, project_id=project_id, policy_id=policy_id, expires_at=expiry
                )
                payload = put_payload(
                    session,
                    store,
                    context,
                    {"requested": requested, "specification": campaign.model_dump(mode="json")},
                    "campaign_envelope",
                    refs,
                    policy,
                )
                row = CampaignRecord(
                    id=identity,
                    project_id=project_id,
                    environment_id=env.id,
                    request_id=body.request_id,
                    dataset_id=dataset.id,
                    policy_id=policy_id,
                    payload_id=payload.id,
                    request_sha256=payload.content_hash,
                    approval_sha256="0" * 64,
                    specification=public_config(
                        campaign.model_dump(mode="json"), redact_content_hashes=True
                    ),
                    economic_envelope=envelope,
                    expires_at=expiry,
                    actor=auth["actor"],
                    status="READY_FOR_APPROVAL" if envelope["eligible"] else "ABSTAINED",
                    summary={
                        "deployment_authorized": False,
                        "code": "approval_required"
                        if envelope["eligible"]
                        else "economic_opportunity_insufficient_or_unknown",
                    },
                )
                row.approval_sha256 = approval_hash(row, payload.content_hash)
                session.add(row)
                session.flush()
                session.add(
                    CampaignEconomicReference(
                        campaign_id=row.id,
                        project_id=project_id,
                        environment_id=env.id,
                        snapshot_id=snapshot.id,
                        snapshot_sha256=snapshot.evidence_sha256,
                    )
                )
                session.add(
                    AuditEvent(
                        project_id=project_id,
                        actor=auth["actor"],
                        action="campaign_prepared",
                        details={"campaign_id": str(row.id), "eligible": envelope["eligible"]},
                    )
                )
                result = brief(row)
            return result
        except ValueError as exc:
            code = str(exc)
            raise HTTPException(
                409,
                code
                if code.isascii() and code.isupper() and len(code) < 120
                else "CAMPAIGN_PREPARATION_FAILED",
            ) from None

    @app.post("/v1/projects/{project_id}/campaigns/draft", response_model=CampaignBrief)
    def draft(project_id: UUID, body: CampaignDraft, auth=Depends(authorize)):
        scope(auth, project_id, ("analyst",))
        try:
            with sessions() as session:
                template = session.get(ExperimentPlanRecord, body.template_plan_id)
                if not template or template.project_id != project_id:
                    raise HTTPException(404, "CAMPAIGN_TEMPLATE_PLAN_NOT_FOUND")
                _, policy = active_policy(
                    session, project_id, UUID(template.specification["policy_id"])
                )
                plan = load_plan(require_store(app), project_id, template.specification)
                if (
                    plan.staged
                    or plan.predecessor_run_id
                    or plan.prompt_bindings
                    or plan.episode_execution
                ):
                    raise HTTPException(409, "CAMPAIGN_REQUIRES_UNSTAGED_NATIVE_TEMPLATE")
                if body.generator_model.provider not in policy.allowed_providers:
                    raise HTTPException(403, "CAMPAIGN_GENERATOR_VENDOR_POLICY_DENIED")
                candidates = [
                    m
                    for m in plan.candidates
                    if m.instruction == plan.baseline.instruction
                    and upper_cost(m) < upper_cost(plan.baseline)
                ]
                simple = (
                    min(candidates, key=lambda m: (upper_cost(m), m.key))
                    if candidates
                    else plan.baseline.model_copy(
                        update={
                            "key": "static-control"
                            if plan.baseline.key != "static-control"
                            else "unchanged-control"
                        }
                    )
                )
                dataset = session.get(DatasetRecord, template.dataset_id)
                raw = plan.model_dump(mode="json")
                raw.update(
                    split="validation",
                    candidates=[simple.model_dump(mode="json")],
                    approved_providers=list(
                        dict.fromkeys([*plan.approved_providers, body.generator_model.provider])
                    ),
                    sample_limit=None,
                    pilot_samples=0,
                    pilot_minimum=None,
                )
                instruction_edit = body.instruction_edit
                if instruction_edit is None and plan.source_binding:
                    instruction_edit = cached_instruction_edits(
                        session, project_id, {plan.source_binding.profile_sha256}
                    ).get(plan.source_binding.profile_sha256)
                specification = ImprovementCampaign.model_validate(
                    {
                        "version": body.version,
                        "dataset_id": str(template.dataset_id),
                        "baseline_snapshot_id": str(body.baseline_snapshot_id),
                        "environment": dataset.provenance["environment"],
                        "template": raw,
                        **(
                            {
                                "static_portfolio": [
                                    m.model_dump(mode="json")
                                    for m in automatic_static_portfolio(
                                        ExperimentPlan.model_validate(raw)
                                    )
                                ]
                            }
                            if body.version == "cutback.improvement-campaign.v3"
                            else {}
                        ),
                        "instruction_edit": instruction_edit.model_dump(mode="json")
                        if instruction_edit
                        else None,
                        "generator": {
                            "model": body.generator_model.model_dump(mode="json"),
                            "rounds": body.rounds,
                            "variants_per_round": body.variants_per_round,
                            "max_output_tokens": plan.baseline.max_output_tokens,
                        },
                        "budget": body.budget.model_dump(mode="json"),
                        "conservative_savings_per_success_usd": (
                            body.conservative_savings_per_success_usd
                        ),
                        "forecast_successful_tasks": body.forecast_successful_tasks,
                    }
                )
            return prepare(
                project_id,
                CampaignInput(request_id=body.request_id, specification=specification),
                auth,
            )
        except ValueError:
            raise HTTPException(409, "CAMPAIGN_DRAFT_CONTRACT_OR_POLICY_INVALID") from None

    @app.get("/v1/projects/{project_id}/campaigns", response_model=CampaignPage)
    def listing(
        project_id: UUID,
        environment_id: UUID | None = None,
        after: UUID | None = None,
        limit: int = Query(20, ge=1, le=50),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            query = select(CampaignRecord).where(CampaignRecord.project_id == project_id)
            if environment_id:
                query = query.where(CampaignRecord.environment_id == environment_id)
            if after:
                query = query.where(CampaignRecord.id > after)
            rows = session.scalars(query.order_by(CampaignRecord.id).limit(limit + 1)).all()
            return CampaignPage(
                items=[brief(r) for r in rows[:limit]],
                next_after=rows[limit - 1].id if len(rows) > limit else None,
            )

    @app.get("/v1/projects/{project_id}/campaigns/{campaign_id}", response_model=CampaignDetail)
    def detail(project_id: UUID, campaign_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id)
        with sessions() as session:
            row = scoped(session, project_id, campaign_id)
            runs = session.execute(
                select(CampaignRun, ExperimentRunRecord)
                .join(ExperimentRunRecord, CampaignRun.run_id == ExperimentRunRecord.id)
                .where(CampaignRun.campaign_id == row.id)
                .order_by(CampaignRun.created_at, CampaignRun.id)
                .limit(8)
            ).all()
            candidates = session.scalars(
                select(CampaignCandidate)
                .where(CampaignCandidate.campaign_id == row.id)
                .order_by(CampaignCandidate.round_index, CampaignCandidate.ordinal)
                .limit(20)
            ).all()
            dispositions = {
                item["candidate_id"]: item["status"]
                for link, _ in runs
                for item in link.derivation.get("candidate_dispositions", [])
            }
            return CampaignDetail(
                **brief(row).model_dump(),
                runs=[
                    {
                        "run_id": str(r.id),
                        "plan_id": str(r.plan_id),
                        "phase": link.phase,
                        "round": link.round_index,
                        "plan_hash": link.plan_sha256,
                        "status": r.status,
                        "requests": r.requests_used,
                        "known_cost_requests": r.known_cost_requests,
                        "reserved_usd": str(r.reserved_usd),
                        "known_spend_subtotal_usd": str(r.spent_usd),
                    }
                    for link, r in runs
                ],
                candidates=[
                    {
                        "id": str(c.id),
                        "key": c.model_key,
                        "kind": c.kind,
                        "disposition": dispositions.get(str(c.id), "simple_baseline"),
                        "round": c.round_index,
                        "configuration_sha256": c.configuration_sha256,
                        "configuration": c.configuration,
                    }
                    for c in candidates
                ],
            )

    @app.post(
        "/v1/projects/{project_id}/campaigns/{campaign_id}/selected/content",
        response_model=CampaignSelectedContent,
    )
    def selected_content(
        project_id: UUID, campaign_id: UUID, body: ContentPurpose, auth=Depends(authorize)
    ):
        scope(auth, project_id, ("admin",))
        try:
            with sessions.begin() as session:
                lock_lineage(session, project_id)
                parent = scoped(session, project_id, campaign_id)
                if (
                    parent.status != "COMPLETED"
                    or parent.requests_used != parent.known_cost_requests
                ):
                    raise HTTPException(409, "CAMPAIGN_COMPLETION_AND_KNOWN_OVERHEAD_REQUIRED")
                store = require_store(app)
                campaign, policy, _, _ = load_campaign(session, store, parent)
                selection = session.get(CampaignSelection, parent.id)
                link = session.scalar(
                    select(CampaignRun).where(
                        CampaignRun.campaign_id == parent.id, CampaignRun.phase == "confirmation"
                    )
                )
                if selection is None or link is None:
                    raise HTTPException(409, "CAMPAIGN_UNTOUCHED_CONFIRMATION_REQUIRED")
                run = session.get(ExperimentRunRecord, link.run_id)
                row = session.get(ExperimentPlanRecord, run.plan_id)
                plan = load_plan(store, project_id, row.specification)
                version = session.get(CampaignCandidate, selection.candidate_id)
                model = candidate_model(session, store, parent, version, policy)
                if (
                    run.status != "COMPLETED"
                    or len(plan.candidates) != 1
                    or plan.candidates[0] != model
                    or row.specification["plan_hash"] != link.plan_sha256
                ):
                    raise HTTPException(409, "CAMPAIGN_SELECTED_CONFIRMATION_CHANGED")
                recs = session.scalars(
                    select(RecommendationRecord)
                    .where(
                        RecommendationRecord.project_id == project_id,
                        RecommendationRecord.run_id == run.id,
                    )
                    .limit(2)
                ).all()
                if (
                    len(recs) != 1
                    or not recs[0].evidence.get("quality_gate_passed")
                    or not recs[0].evidence.get("cheaper_on_sample")
                ):
                    raise HTTPException(409, "CAMPAIGN_CONFIRMED_CANDIDATE_DID_NOT_PASS_GATES")
                rec = recs[0]
                if rec.status == "rejected":
                    raise HTTPException(409, "CAMPAIGN_SELECTED_RECOMMENDATION_REJECTED")
                from cutback.server.dataset_lineage import require_current_test_evidence

                require_current_test_evidence(session, store, rec)
                evidence = dict(rec.evidence)
                evidence["candidate"] = model.model_dump(mode="json")
                evidence["rollback"] = {
                    **evidence["rollback"],
                    "baseline": plan.baseline.model_dump(mode="json"),
                }
                session.add(
                    AuditEvent(
                        project_id=project_id,
                        actor=auth["actor"],
                        action="campaign_selected_content_read",
                        details={"campaign_id": str(parent.id), "purpose": body.purpose},
                    )
                )
                return CampaignSelectedContent(
                    campaign_id=parent.id,
                    campaign_hash=parent.approval_sha256,
                    run_id=run.id,
                    plan_id=row.id,
                    plan_hash=link.plan_sha256,
                    plan=plan,
                    recommendation_id=rec.id,
                    recommendation_status=rec.status,
                    evidence=evidence,
                    instruction_edit=campaign.instruction_edit,
                    max_instruction_bytes=campaign.generator.max_instruction_bytes,
                )
        except ValueError:
            raise HTTPException(
                409, "CAMPAIGN_CURRENT_SOURCE_EVIDENCE_OR_CONTENT_INVALID"
            ) from None

    @app.post(
        "/v1/projects/{project_id}/campaigns/{campaign_id}/candidates/{candidate_id}/content",
        response_model=CampaignCandidateContent,
    )
    def candidate_content(
        project_id: UUID,
        campaign_id: UUID,
        candidate_id: UUID,
        body: ContentPurpose,
        auth=Depends(authorize),
    ):
        scope(auth, project_id, ("admin",))
        try:
            with sessions.begin() as session:
                parent = scoped(session, project_id, campaign_id)
                candidate = session.get(CampaignCandidate, candidate_id)
                if (
                    not candidate
                    or candidate.project_id != project_id
                    or candidate.campaign_id != parent.id
                ):
                    raise HTTPException(404, "CAMPAIGN_CANDIDATE_NOT_FOUND")
                policy = current_policy(
                    session,
                    project_id,
                    parent.policy_id,
                    parent.specification["environment"],
                    parent.specification["template"]["workflow"],
                )
                model = candidate_model(session, require_store(app), parent, candidate, policy)
                session.add(
                    AuditEvent(
                        project_id=project_id,
                        actor=auth["actor"],
                        action="campaign_candidate_content_read",
                        details={
                            "campaign_id": str(parent.id),
                            "candidate_id": str(candidate.id),
                            "purpose": body.purpose,
                        },
                    )
                )
                return CampaignCandidateContent(
                    id=candidate.id,
                    configuration_sha256=candidate.configuration_sha256,
                    configuration=model,
                )
        except ValueError:
            raise HTTPException(
                409, "CAMPAIGN_CANDIDATE_CONTENT_EXPIRED_CHANGED_OR_DENIED"
            ) from None

    @app.post(
        "/v1/projects/{project_id}/campaigns/{campaign_id}/approve", response_model=CampaignBrief
    )
    def approve(
        project_id: UUID, campaign_id: UUID, body: CampaignApproval, auth=Depends(authorize)
    ):
        scope(auth, project_id, ("admin",))
        try:
            with sessions.begin() as session:
                lock_lineage(session, project_id)
                row = scoped(session, project_id, campaign_id, lock=True)
                if row.approval_status != "pending" or row.status != "READY_FOR_APPROVAL":
                    raise HTTPException(409, "CAMPAIGN_ALREADY_DECIDED_OR_INELIGIBLE")
                if body.campaign_hash != row.approval_sha256:
                    raise HTTPException(409, "CAMPAIGN_HASH_CHANGED")
                campaign, _, _, _ = load_campaign(session, require_store(app), row)
                now = datetime.now(UTC)
                row.approval_status, row.approved_by, row.status = (
                    "consumed",
                    auth["actor"],
                    "QUEUED",
                )
                row.started_at, row.deadline_at = (
                    now,
                    min(row.expires_at, now + timedelta(seconds=campaign.budget.max_wall_seconds)),
                )
                row.available_at = now
                row.summary = {
                    "code": "approved_for_bounded_execution",
                    "deployment_authorized": False,
                }
                session.add(
                    AuditEvent(
                        project_id=project_id,
                        actor=auth["actor"],
                        action="campaign_budget_approved",
                        details={
                            "campaign_id": str(row.id),
                            "campaign_hash": body.campaign_hash,
                            "approval_ref": body.approval_ref,
                        },
                    )
                )
                return brief(row)
        except ValueError:
            raise HTTPException(409, "CAMPAIGN_CURRENT_AUTHORITY_OR_INPUTS_INVALID") from None

    @app.post(
        "/v1/projects/{project_id}/campaigns/{campaign_id}/cancel", response_model=CampaignBrief
    )
    def cancel(project_id: UUID, campaign_id: UUID, body: CampaignCancel, auth=Depends(authorize)):
        scope(auth, project_id, ("editor",))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = scoped(session, project_id, campaign_id, lock=True)
            if row.status in {"COMPLETED", "FAILED", "CANCELLED", "ABSTAINED"}:
                raise HTTPException(409, "CAMPAIGN_TERMINAL")
            row.status, row.completed_at = "CANCELLED", datetime.now(UTC)
            row.summary = {
                "code": "cancelled_by_owner",
                "deployment_authorized": False,
                "unsettled_reservations": row.requests_used - row.known_cost_requests,
            }
            row.lease_token = row.lease_until = None
            runs = session.scalars(
                select(ExperimentRunRecord)
                .join(CampaignRun, CampaignRun.run_id == ExperimentRunRecord.id)
                .where(CampaignRun.campaign_id == row.id)
                .with_for_update(of=ExperimentRunRecord)
            ).all()
            for run in runs:
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
                    action="campaign_cancelled",
                    details={"campaign_id": str(row.id)},
                )
            )
            return brief(row)
