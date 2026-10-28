"""Scoped campaign admission and current authority, shared by APIs and workers."""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from cutback.core.instruction_edits import LiteralInstructionEdit
from cutback.evals.campaigns import ImprovementCampaign
from cutback.evals.datasets import digest
from cutback.evals.experiments import ModelSpec
from cutback.server.campaign_content import current_policy, read_payload
from cutback.server.campaign_db import CampaignEconomicReference, CampaignRecord, CampaignRun
from cutback.server.db import DatasetRecord, Environment, ScanRun
from cutback.server.economics_api import TaskWindow
from cutback.server.economics_db import TaskCostSnapshot
from cutback.server.plan_artifacts import public_config
from cutback.server.principals import principal_role
from cutback.server.traces import fingerprint


def cached_instruction_edits(session, project, profile_ids):
    if not profile_ids:
        return {}
    rows = session.scalars(
        select(CampaignRecord.specification["instruction_edit"])
        .where(
            CampaignRecord.project_id == project,
            CampaignRecord.specification["instruction_edit"]["source_profile_sha256"]
            .as_string()
            .in_(profile_ids),
        )
        .order_by(CampaignRecord.created_at.desc())
        .limit(1000)
    ).all()
    result: dict[str, LiteralInstructionEdit] = {}
    for value in rows:
        try:
            binding = LiteralInstructionEdit.model_validate(value)
        except ValueError:
            continue
        result.setdefault(binding.source_profile_sha256, binding)
    return result


def candidate_model(session, store, parent, candidate, policy):
    raw, sha = read_payload(session, store, parent, candidate.payload_id, policy)
    model = ModelSpec.model_validate(raw)
    if (
        sha != candidate.configuration_sha256
        or model.key != candidate.model_key
        or public_config(model.model_dump(mode="json"), redact_content_hashes=True)
        != candidate.configuration
    ):
        raise ValueError("CAMPAIGN_FROZEN_CANDIDATE_CHANGED")
    return model


def approval_hash(parent, payload_hash):
    return digest(
        {
            "contract": "cutback.campaign-approval.v1",
            "campaign_id": str(parent.id),
            "project_id": str(parent.project_id),
            "environment_id": str(parent.environment_id),
            "dataset_id": str(parent.dataset_id),
            "policy_id": str(parent.policy_id),
            "payload_sha256": payload_hash,
            "economic_envelope": parent.economic_envelope,
            "expires_at": parent.expires_at.isoformat(),
        }
    )


def snapshot_cost(snapshot, project, environment, workflow):
    if not snapshot or (snapshot.project_id, snapshot.environment_id) != (project, environment):
        raise ValueError("CAMPAIGN_BASELINE_SNAPSHOT_NOT_FOUND")
    if fingerprint(snapshot.report) != snapshot.evidence_sha256:
        raise ValueError("CAMPAIGN_BASELINE_SNAPSHOT_INTEGRITY_FAILURE")
    report = TaskWindow.model_validate(snapshot.report)
    identities = {
        (i.declaration.workflow, i.declaration.service, i.declaration.release) for i in report.items
    }
    if (
        len(identities) != 1
        or next(iter(identities))[0] != workflow
        or next(iter(identities))[2] is None
    ):
        raise ValueError("CAMPAIGN_BASELINE_REQUIRES_ONE_WORKFLOW_SERVICE_RELEASE")
    summary = report.summary
    return (
        Decimal(summary.declared_window_cost_per_success_usd)
        if summary.window_cost_complete_under_declaration
        and summary.declared_window_cost_per_success_usd is not None
        else None
    )


def require_source_registered(session, project, plan):
    if not plan.source_binding:
        return
    profiles = session.scalars(
        select(ScanRun)
        .where(ScanRun.project_id == project)
        .order_by(ScanRun.created_at.desc())
        .limit(100)
    ).all()
    binding = plan.source_binding.model_dump(mode="json")
    if not any(binding in row.inventory.get("request_profiles", []) for row in profiles):
        raise ValueError("CAMPAIGN_SOURCE_REQUEST_PROFILE_NOT_REGISTERED")


def load_campaign(session, store, parent):
    if parent.expires_at <= datetime.now(UTC):
        raise ValueError("CAMPAIGN_ENVELOPE_EXPIRED")
    policy = current_policy(
        session,
        parent.project_id,
        parent.policy_id,
        parent.specification["environment"],
        parent.specification["template"]["workflow"],
    )
    content, payload_hash = read_payload(session, store, parent, parent.payload_id, policy)
    campaign = ImprovementCampaign.model_validate(content["specification"])
    if (
        approval_hash(parent, payload_hash) != parent.approval_sha256
        or public_config(campaign.model_dump(mode="json"), redact_content_hashes=True)
        != parent.specification
    ):
        raise ValueError("CAMPAIGN_ENVELOPE_CHANGED_REAPPROVAL_REQUIRED")
    dataset = session.get(DatasetRecord, parent.dataset_id)
    env = session.get(Environment, parent.environment_id)
    if (
        not dataset
        or dataset.project_id != parent.project_id
        or not env
        or env.project_id != parent.project_id
        or env.name != campaign.environment
        or dataset.immutable_hash != campaign.template.dataset_hash
        or dataset.workflow_id != campaign.template.workflow
        or dataset.provenance.get("environment") != campaign.environment
        or str(parent.policy_id) != dataset.provenance.get("policy_id")
    ):
        raise ValueError("CAMPAIGN_DATASET_ENVIRONMENT_OR_POLICY_CHANGED")
    if any(
        provider not in policy.allowed_providers
        for provider in campaign.template.approved_providers
    ):
        raise ValueError("CAMPAIGN_VENDOR_POLICY_DENIED")
    require_source_registered(session, parent.project_id, campaign.template)
    reference = session.get(CampaignEconomicReference, parent.id)
    snapshot = session.get(TaskCostSnapshot, reference.snapshot_id) if reference else None
    if (
        not reference
        or snapshot is None
        or reference.snapshot_sha256 != snapshot.evidence_sha256
        or snapshot_cost(
            snapshot, parent.project_id, parent.environment_id, campaign.template.workflow
        )
        is None
    ):
        raise ValueError("CAMPAIGN_BASELINE_ECONOMIC_CONTEXT_EXPIRED_OR_CHANGED")
    return campaign, policy, dataset, content


def authorize_campaign(session, store, parent, *, synthetic_transport=False):
    from cutback.server.campaign_ledger import ACTIVE

    now = datetime.now(UTC)
    if (
        parent.approval_status != "consumed"
        or parent.status not in ACTIVE
        or parent.deadline_at is None
        or parent.deadline_at <= now
        or principal_role(session, parent.project_id, parent.approved_by or "")
        not in {"admin", "project_admin"}
    ):
        raise ValueError("CAMPAIGN_CANCELLED_EXPIRED_OR_AUTHORITY_REVOKED")
    campaign, policy, dataset, content = load_campaign(session, store, parent)
    if campaign.generator.transport == "scripted_test_only" and synthetic_transport is not True:
        raise ValueError("SCRIPTED_CAMPAIGN_REQUIRES_EXPLICIT_SYNTHETIC_EXECUTOR")
    return campaign, policy, dataset, content


def authorize_campaign_run(session, store, run_id, *, synthetic_transport=False):
    link = session.scalar(select(CampaignRun).where(CampaignRun.run_id == run_id))
    if link:
        parent = session.get(CampaignRecord, link.campaign_id, populate_existing=True)
        if not parent or link.campaign_sha256 != parent.approval_sha256:
            raise ValueError("CAMPAIGN_CHILD_DERIVATION_CHANGED")
        authorize_campaign(session, store, parent, synthetic_transport=synthetic_transport)
