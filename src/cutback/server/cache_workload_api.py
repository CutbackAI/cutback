"""Source-scoped cache trial eligibility from frozen observed workload metadata."""

from decimal import Decimal, DecimalException, localcontext
from uuid import UUID

from fastapi import Depends, HTTPException

from cutback.core.cache_workload import CACHE_WORKLOAD_CONTRACT, CacheWorkloadAssessment
from cutback.core.exact_cache import ExactCacheExecution
from cutback.core.models import Identifier, LLMEvent, StrictModel
from cutback.core.request_profiles import SourceBinding
from cutback.evals.datasets import digest
from cutback.server.db import AuditEvent
from cutback.server.native_profile_db import NativeProfileAnalysis
from cutback.server.native_profile_metadata import metadata_digest, retained_metadata
from cutback.server.native_profile_service import authority


class CacheWorkloadInput(StrictModel):
    source_binding: SourceBinding
    baseline_model: Identifier
    cache_execution: ExactCacheExecution


def assess_cache_workload(session, analysis, body):
    policy = authority(session, analysis)
    if analysis.state != "COMPLETED" or not analysis.summary or analysis.completed_at is None:
        raise HTTPException(409, "CACHE_WORKLOAD_COMPLETED_ANALYSIS_REQUIRED")
    summary = analysis.summary
    # Late-arriving canonical attempts also invalidate the earlier census.
    rows = retained_metadata(session, analysis, as_of=False)
    reasons = []
    expected = summary.get("metadata_evidence_sha256")
    if not expected or len(rows) > 1000 or metadata_digest(rows) != expected:
        reasons.append("RETAINED_METADATA_SNAPSHOT_UNAVAILABLE_OR_CHANGED")
    if summary.get("cache_trial_eligible") is not True:
        reasons.append("RETAINED_STREAM_NOT_CACHE_TRIAL_ELIGIBLE")
    binding, execution = body.source_binding, body.cache_execution
    if binding.workflow_id != analysis.workflow:
        reasons.append("SOURCE_WORKFLOW_MISMATCH")
    if binding.source_commit is None:
        reasons.append("SOURCE_COMMIT_UNKNOWN")
    if (
        execution.freshness_ref != policy.native_request_profile.freshness_ref
        or execution.ttl_seconds != policy.native_request_profile.prospective_ttl_seconds
    ):
        reasons.append("APPROVED_FRESHNESS_OR_TTL_MISMATCH")
    known = Decimal(0)
    known_count = 0
    for row in rows:
        event = LLMEvent.model_validate(row.event)
        if (
            event.provider != "openai"
            or event.sdk_surface != "responses.create"
            or event.call_site_id != binding.call_site_id
            or event.source_commit != binding.source_commit
            or event.requested_model != body.baseline_model
        ):
            reasons.append("RETAINED_SOURCE_CALLSITE_COMMIT_OR_MODEL_MISMATCH")
        if event.traffic_kind != "application" or event.metering_status != "text_only":
            reasons.append("APPLICATION_ATTEMPT_SCOPE_UNQUALIFIED")
        if (
            row.cost_usd is not None
            and row.cost_status == "measured_usage_modeled_price"
            and row.cost_reason is None
            and row.price_snapshot_id
            and event.usage_source == "provider_reported"
        ):
            known += row.cost_usd
            known_count += 1
    n, hits = summary.get("profiled_attempts"), summary.get("modeled_potential_hits")
    if (
        type(n) is not int
        or type(hits) is not int
        or not 0 < hits < n <= 1000
        or len(rows) != n
        or summary.get("metadata_attempts") != n
    ):
        reasons.append("COMPLETE_BOUNDED_NATIVE_WORKLOAD_REQUIRED")
    modeled_avoided = summary.get("modeled_avoided_provider_cost_usd")
    operations = total_cache_cost = net = None
    if modeled_avoided is None or not rows or known_count != len(rows):
        reasons.append("SOURCE_MATCHED_PROVIDER_COST_COVERAGE_UNKNOWN")
    elif not reasons:
        with localcontext() as context:
            context.prec = 64
            avoided = Decimal(modeled_avoided)
            if not avoided.is_finite() or not Decimal(0) <= avoided <= known:
                reasons.append("RETAINED_PROVIDER_COST_CONFLICT")
            else:
                operations = 2 * n - hits
                total_cache_cost = execution.operation_cost_upper_usd * operations
                net = avoided - total_cache_cost
                if net <= 0:
                    reasons.append("NO_POSITIVE_MODELED_NATIVE_WINDOW_OPPORTUNITY")
    result = {
        "version": CACHE_WORKLOAD_CONTRACT,
        "project_id": str(analysis.project_id),
        "environment": analysis.environment,
        "workflow": analysis.workflow,
        "analysis_id": str(analysis.id),
        "analysis_sha256": digest(summary),
        "source_binding_sha256": digest(binding.model_dump(mode="json")),
        "baseline_model": body.baseline_model,
        "cache_execution_sha256": digest(execution.model_dump(mode="json")),
        "scope": "retained_client_reported_source_window",
        "source_lineage": "client_reported_commit_and_callsite_not_independent_attestation",
        "valid_until": analysis.expires_at.isoformat(),
        "metadata_as_of": analysis.completed_at.isoformat(),
        "decision": "prepare_separate_cache_trial" if not reasons else "abstain",
        "reasons": sorted(set(reasons)),
        "profiled_attempts": n,
        "modeled_potential_hits": hits,
        "provider_modeled_window_cost_usd": str(known)
        if rows and known_count == len(rows)
        else None,
        "modeled_avoided_provider_cost_usd": modeled_avoided,
        "declared_cache_operations": operations,
        "declared_cache_operation_upper_cost_usd": str(total_cache_cost)
        if total_cache_cost is not None
        else None,
        "modeled_net_native_window_opportunity_usd": str(net) if net is not None else None,
        "production_hit_rate": None,
        "whole_task_cost_usd": None,
        "realized_savings_usd": None,
        "quality_evidence": "separate_independent_trial_required",
        "cache_delivery_benchmarked": False,
        "paid_calls_authorized": False,
        "source_change_authorized": False,
        "provider_calls": 0,
        "encrypted_storage_reads": 0,
    }
    return CacheWorkloadAssessment.model_validate({**result, "assessment_sha256": digest(result)})


def install_cache_workload(app, sessions, authorize, scope):
    authorized = Depends(authorize)

    @app.post(
        "/v1/projects/{project_id}/native-request-profiles/analyses/{analysis_id}/cache-trial",
        response_model=CacheWorkloadAssessment,
    )
    def assess(project_id: UUID, analysis_id: UUID, body: CacheWorkloadInput, auth=authorized):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            row = session.get(NativeProfileAnalysis, analysis_id)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "NATIVE_PROFILE_ANALYSIS_NOT_FOUND")
            try:
                receipt = assess_cache_workload(session, row, body)
                session.add(
                    AuditEvent(
                        project_id=project_id,
                        actor=auth["actor"],
                        action="cache_workload_assessed",
                        details={
                            "analysis_id": str(row.id),
                            "assessment_sha256": receipt.assessment_sha256,
                            "source_binding_sha256": receipt.source_binding_sha256,
                            "decision": receipt.decision,
                        },
                    )
                )
                return receipt
            except (ValueError, DecimalException):
                raise HTTPException(409, "CURRENT_SOURCE_WORKLOAD_EVIDENCE_REQUIRED") from None
