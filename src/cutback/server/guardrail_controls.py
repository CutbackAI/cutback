"""Encrypted policy registration, exact approval and shared metadata-only intents."""

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, field_validator
from sqlalchemy import func, select

from cutback.core.guardrails import GuardrailBinding, GuardrailDecision, GuardrailPolicy
from cutback.core.models import StrictModel
from cutback.core.tool_policy import canonical
from cutback.evals.datasets import digest
from cutback.guardrails import strict_json
from cutback.server.campaign_content import current_policy
from cutback.server.content_policy import policy_content
from cutback.server.dataset_lineage import lock_lineage
from cutback.server.db import AuditEvent, CapturePolicyRecord, PayloadSample, Project
from cutback.server.evaluation_api import policy_object_count
from cutback.server.guardrail_db import GuardrailOperationRow, GuardrailPolicyRow
from cutback.server.principals import principal_role


class GuardrailRegistration(StrictModel):
    service_id: UUID
    capture_policy_id: UUID
    policy: GuardrailPolicy
    max_checks: int = Field(default=10000, strict=True, ge=2, le=10000000)

    @field_validator("policy", mode="before")
    @classmethod
    def frozen_nonce_required(cls, value):
        if isinstance(value, dict) and "content_nonce" not in value:
            raise ValueError("GUARDRAIL_FROZEN_CONTENT_NONCE_REQUIRED")
        return value


class GuardrailActivation(StrictModel):
    approval_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    approval_ref: UUID
    expected_state_version: int = Field(strict=True, ge=0, le=2)


class GuardrailDisable(StrictModel):
    approval_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_state_version: int = Field(strict=True, ge=0, le=2)


def _deny(code, status=409):
    raise HTTPException(status, code)


def _lock(session, project):
    lock_lineage(session, project)
    if session.get(Project, project, with_for_update=True) is None:
        _deny("GUARDRAIL_PROJECT_NOT_FOUND", 404)


def _admin(session, project, actor):
    if principal_role(session, project, actor) not in {"admin", "project_admin"}:
        _deny("GUARDRAIL_CURRENT_ADMIN_REQUIRED", 403)


def approval_hash(binding, capture_policy_id, max_checks):
    return hashlib.sha256(
        canonical(
            {
                "binding": binding.model_dump(mode="json"),
                "capture_policy_id": str(capture_policy_id),
                "max_checks": max_checks,
            }
        )
    ).hexdigest()


def binding_for(row):
    try:
        binding = GuardrailBinding.model_validate(row.binding)
        valid = (binding.policy_id, binding.project_id) == (
            row.id,
            row.project_id,
        ) and hmac.compare_digest(
            approval_hash(binding, row.capture_policy_id, row.max_checks), row.approval_hash
        )
    except Exception:
        valid = False
    if not valid:
        _deny("GUARDRAIL_POLICY_METADATA_INTEGRITY_FAILURE")
    return binding


def _rights(session, row, binding):
    payload = session.get(PayloadSample, row.payload_id)
    if (
        payload is None
        or payload.project_id != row.project_id
        or payload.policy_id != row.capture_policy_id
        or payload.expires_at <= datetime.now(UTC)
    ):
        _deny("GUARDRAIL_ENCRYPTED_POLICY_MISSING_OR_EXPIRED", 403)
    for workflow in binding.workflows:
        try:
            content = current_policy(
                session, row.project_id, row.capture_policy_id, binding.environment, workflow
            )
        except ValueError:
            _deny("GUARDRAIL_CONTENT_AUTHORITY_NOT_CURRENT", 403)
        if binding.expires_at > content.expires_at or binding.expires_at > payload.expires_at:
            _deny("GUARDRAIL_CONTENT_AUTHORITY_WINDOW_TOO_SHORT", 403)
    return payload


def metadata(row, session=None):
    binding = binding_for(row)
    authority = "inactive" if row.state != "APPROVED" else "unknown"
    if row.state == "APPROVED" and session is not None:
        try:
            current_authority(session, row.project_id, row.id, binding)
            authority = "current"
        except HTTPException:
            authority = "ended"
    return {
        "id": str(row.id),
        "binding": binding.model_dump(mode="json"),
        "capture_policy_id": str(row.capture_policy_id),
        "approval_hash": row.approval_hash,
        "state": "EXPIRED" if datetime.now(UTC) >= binding.expires_at else row.state,
        "state_version": row.state_version,
        "max_checks": row.max_checks,
        "checks_used": row.checks_used,
        "approved_at": row.approved_at,
        "created_at": row.created_at,
        "execution_authority": authority,
    }


def register(session, store, project, body, actor, refs):
    _lock(session, project)
    _admin(session, project, actor)
    binding = GuardrailBinding(service_id=body.service_id, **body.policy.public_metadata())
    now = datetime.now(UTC)
    if binding.project_id != project:
        _deny("GUARDRAIL_PROJECT_SCOPE_DENIED", 403)
    if binding.starts_at < now - timedelta(minutes=5) or binding.expires_at <= now:
        _deny("GUARDRAIL_REGISTRATION_WINDOW_INVALID", 422)
    expected = approval_hash(binding, body.capture_policy_id, body.max_checks)
    prior = session.get(GuardrailPolicyRow, binding.policy_id)
    if prior:
        if prior.project_id != project or prior.approval_hash != expected:
            _deny("GUARDRAIL_IMMUTABLE_POLICY_ID_CONFLICT")
        return prior
    if (
        session.scalar(
            select(func.count())
            .select_from(GuardrailPolicyRow)
            .where(GuardrailPolicyRow.project_id == project)
        )
        >= 256
    ):
        _deny("GUARDRAIL_POLICY_CAPACITY_REACHED", 429)
    capture = session.get(CapturePolicyRecord, body.capture_policy_id, with_for_update=True)
    if capture is None or capture.project_id != project:
        _deny("GUARDRAIL_CAPTURE_SCOPE_DENIED", 403)
    rights = None
    for workflow in binding.workflows:
        try:
            rights = current_policy(session, project, capture.id, binding.environment, workflow)
        except ValueError:
            _deny("GUARDRAIL_CONTENT_AUTHORITY_NOT_CURRENT", 403)
    assert rights is not None
    expiry = min(rights.expires_at, now + timedelta(days=rights.retention_days))
    if binding.expires_at > expiry:
        _deny("GUARDRAIL_CONTENT_AUTHORITY_WINDOW_TOO_SHORT", 403)
    if policy_object_count(session, capture.id) >= rights.max_samples:
        _deny("GUARDRAIL_SHARED_CONTENT_QUOTA_EXHAUSTED", 429)
    wrapper = {"value": body.policy.model_dump(mode="json")}
    try:
        clean, blob = policy_content(wrapper, rights, preserve_semantics=True)
        ref = store.put(project, blob, rights.retention_days)
    except (ValueError, OSError):
        _deny("GUARDRAIL_POLICY_CONTENT_UNAVAILABLE_OR_REDACTION_REQUIRED", 422)
    refs.append(ref)
    payload = PayloadSample(
        project_id=project,
        policy_id=capture.id,
        object_ref=ref,
        content_hash=digest(clean),
        expires_at=expiry,
        metadata_safe={"kind": "runtime_guardrail_policy", "policy_id": str(binding.policy_id)},
    )
    session.add(payload)
    session.flush()
    row = GuardrailPolicyRow(
        id=binding.policy_id,
        project_id=project,
        capture_policy_id=capture.id,
        payload_id=payload.id,
        binding=binding.model_dump(mode="json"),
        approval_hash=expected,
        max_checks=body.max_checks,
        created_by=actor,
    )
    session.add(row)
    session.flush()
    session.add(
        AuditEvent(
            project_id=project,
            actor=actor,
            action="runtime_guardrail_registered",
            details={"policy_id": str(row.id)},
        )
    )
    return row


def scoped(session, project, policy_id):
    row = session.get(GuardrailPolicyRow, policy_id, populate_existing=True)
    if row is None or row.project_id != project:
        _deny("GUARDRAIL_POLICY_NOT_FOUND", 404)
    return row


def activate(session, project, policy_id, body, actor):
    _lock(session, project)
    _admin(session, project, actor)
    row = scoped(session, project, policy_id)
    binding = binding_for(row)
    if row.approval_hash != body.approval_hash or not body.approval_ref.int:
        _deny("GUARDRAIL_EXACT_APPROVAL_REQUIRED")
    if (
        row.state == "APPROVED"
        and row.approval_ref == body.approval_ref
        and row.state_version == body.expected_state_version + 1
    ):
        current_authority(session, project, policy_id, binding)
        return row
    if row.state != "PENDING" or row.state_version != body.expected_state_version:
        _deny("GUARDRAIL_STATE_VERSION_CONFLICT")
    if binding.expires_at <= datetime.now(UTC):
        _deny("GUARDRAIL_POLICY_EXPIRED", 403)
    _rights(session, row, binding)
    row.state, row.state_version = "APPROVED", 1
    row.approved_by, row.approval_ref, row.approved_at = actor, body.approval_ref, datetime.now(UTC)
    session.add(
        AuditEvent(
            project_id=project,
            actor=actor,
            action="runtime_guardrail_approved",
            details={"policy_id": str(row.id), "approval_hash": row.approval_hash},
        )
    )
    return row


def disable(session, project, policy_id, body, actor):
    _lock(session, project)
    _admin(session, project, actor)
    row = scoped(session, project, policy_id)
    if row.approval_hash != body.approval_hash or row.state_version != body.expected_state_version:
        _deny("GUARDRAIL_STATE_VERSION_CONFLICT")
    if row.state != "DISABLED":
        row.state, row.state_version = "DISABLED", row.state_version + 1
        session.add(
            AuditEvent(
                project_id=project,
                actor=actor,
                action="runtime_guardrail_disabled",
                details={"policy_id": str(row.id)},
            )
        )
    return row


def current_authority(session, project, policy_id, expected_binding):
    row = scoped(session, project, policy_id)
    binding = binding_for(row)
    now = datetime.now(UTC)
    if (
        row.state != "APPROVED"
        or binding != expected_binding
        or not binding.starts_at <= now < binding.expires_at
    ):
        _deny("GUARDRAIL_APPROVAL_NOT_CURRENT", 403)
    _admin(session, project, row.approved_by or "")
    _rights(session, row, binding)
    return row


def load_private_policy(session, store, row, actor, purpose):
    _admin(session, row.project_id, actor)
    binding = binding_for(row)
    payload = _rights(session, row, binding)
    try:
        raw = strict_json(store.get(row.project_id, payload.object_ref))
        definition = GuardrailPolicy.model_validate(raw["value"])
        if (
            digest(raw) != payload.content_hash
            or GuardrailBinding(service_id=binding.service_id, **definition.public_metadata())
            != binding
        ):
            raise ValueError("INTEGRITY")
    except Exception:
        _deny("GUARDRAIL_PRIVATE_POLICY_UNAVAILABLE_OR_CHANGED", 409)
    session.add(
        AuditEvent(
            project_id=row.project_id,
            actor=actor,
            action="runtime_guardrail_content_read",
            details={"policy_id": str(row.id), "purpose": purpose},
        )
    )
    return definition


def reserve_check(session, project, policy_id, expected_binding, operation_id, stage):
    """Persist before HTTP. Existing intents never authorize redispatch by themselves."""
    _lock(session, project)
    row = current_authority(session, project, policy_id, expected_binding)
    if not operation_id.int or stage not in expected_binding.protected_stages:
        _deny("GUARDRAIL_OPERATION_SCOPE_DENIED", 403)
    key = (project, policy_id, operation_id, stage)
    if session.get(GuardrailOperationRow, key) is not None:
        _deny("GUARDRAIL_CHECK_ALREADY_RESERVED_NO_AUTOMATIC_RETRY")
    if row.checks_used >= row.max_checks:
        _deny("GUARDRAIL_SHARED_CHECK_QUOTA_EXHAUSTED", 429)
    row.checks_used += 1
    operation = GuardrailOperationRow(
        project_id=project, policy_id=policy_id, operation_id=operation_id, stage=stage
    )
    session.add(operation)
    session.flush()
    return operation


def finish_check(session, project, policy_id, expected_binding, decision: GuardrailDecision):
    _lock(session, project)
    row = session.get(
        GuardrailOperationRow, (project, policy_id, decision.operation_id, decision.stage)
    )
    if (
        row is None
        or decision.policy_id != policy_id
        or decision.policy_sha256 != expected_binding.policy_sha256
        or decision.checks_expected != expected_binding.stage_rule_counts.get(decision.stage)
        or decision.status == "unavailable"
        and decision.action
        != ("allow" if expected_binding.on_unavailable == "fail_open" else "deny")
    ):
        _deny("GUARDRAIL_CHECK_RECEIPT_SCOPE_DENIED", 403)
    if row.state != "RESERVED":
        _deny("GUARDRAIL_CHECK_ALREADY_FINALIZED")
    try:
        current_authority(session, project, policy_id, expected_binding)
    except HTTPException:
        # Persist the lost-authority outcome, then the caller must deny delivery.
        decision = GuardrailDecision(
            operation_id=decision.operation_id,
            policy_id=policy_id,
            policy_sha256=expected_binding.policy_sha256,
            stage=decision.stage,
            status="blocked",
            action="deny",
            code="GUARDRAIL_SCOPE_DENIED",
            checks_completed=0,
            checks_expected=expected_binding.stage_rule_counts[decision.stage],
            coverage_complete=False,
        )
    row.decision = decision.model_dump(mode="json")
    row.state = "UNKNOWN" if decision.status == "unavailable" else "COMPLETED"
    row.completed_at = datetime.now(UTC)
    return decision
