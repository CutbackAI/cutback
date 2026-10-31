"""Find current source-matched workload evidence without inference or encrypted reads."""

from datetime import UTC, datetime
from uuid import UUID

from cutback.core.cache_workload import CACHE_WORKLOAD_CONTRACT, CacheWorkloadAssessment
from cutback.core.exact_cache import ExactCacheExecution
from cutback.core.models import Identifier
from cutback.core.request_profiles import fingerprint


def clean_source(root):
    from cutback.application_checks import snapshot

    # Qualified plumbing reads bind bytes without invoking repository clean filters.
    return snapshot(root)


def source_input(root, *, cache_execution, workflow=None):
    """Export only the current source binding and declared cache contract for review."""
    from cutback.agent import load_session, selected_profile, workspace
    from cutback.agent_strategy import select_preparation
    from cutback.core.workspace import workspace_lock

    contract = ExactCacheExecution.model_validate(cache_execution)
    root, directory = workspace(root)
    with workspace_lock(directory):
        state = load_session(root)
        if state["phase"] not in {"ANALYZED", "SOURCE_CHANGED", "PLAN_READY"}:
            raise ValueError("ASSESS_WORKLOAD_BEFORE_STARTING_EXECUTION")
        selected = select_preparation(state, workflow)
        if selected["mode"] != "native_search":
            raise ValueError("CACHE_WORKLOAD_REQUIRES_QUALIFIED_NATIVE_SOURCE_ADAPTER")
        profile = selected_profile(root, state, selected["workflow_id"])
        clean_source(root)
        if (
            profile.provider != "openai"
            or profile.request.sdk_surface != "responses.create"
            or profile.binding.source_commit is None
        ):
            raise ValueError("CACHE_WORKLOAD_CLEAN_COMMITTED_NATIVE_RESPONSES_SOURCE_REQUIRED")
        return {
            "source_binding": profile.binding.model_dump(mode="json"),
            "baseline_model": profile.model,
            "cache_execution": contract.model_dump(mode="json"),
        }


def assess(
    root,
    control,
    project,
    *,
    cache_execution,
    workflow=None,
    environment="staging",
    analysis_id=None,
):
    from pydantic import TypeAdapter

    from cutback.agent import load_session, save_session, selected_profile, workspace
    from cutback.agent_strategy import select_preparation
    from cutback.core.workspace import workspace_lock

    project = str(UUID(str(project)))
    environment = TypeAdapter(Identifier).validate_python(environment)
    contract = ExactCacheExecution.model_validate(cache_execution)
    root, directory = workspace(root)
    with workspace_lock(directory):
        state = load_session(root)
        if state["phase"] not in {"ANALYZED", "SOURCE_CHANGED", "PLAN_READY"}:
            raise ValueError("ASSESS_WORKLOAD_BEFORE_STARTING_EXECUTION")
        selected = select_preparation(state, workflow)
        if selected["mode"] != "native_search":
            raise ValueError("CACHE_WORKLOAD_REQUIRES_QUALIFIED_NATIVE_SOURCE_ADAPTER")
        profile = selected_profile(root, state, selected["workflow_id"])
        if profile.provider != "openai" or profile.request.sdk_surface != "responses.create":
            raise ValueError("CACHE_WORKLOAD_REQUIRES_QUALIFIED_NATIVE_RESPONSES_SOURCE")
        source = clean_source(root)
        if profile.binding.source_commit is None:
            raise ValueError("CACHE_WORKLOAD_CLEAN_COMMITTED_SOURCE_REQUIRED")
        original = state["id"], state["analysis_sha256"], state["revision"]
        body = {
            "source_binding": profile.binding.model_dump(mode="json"),
            "baseline_model": profile.model,
            "cache_execution": contract.model_dump(mode="json"),
        }
    ready = control("GET", "/v1/health/ready")
    if ready.get("cache_workload_assessment_contract") != CACHE_WORKLOAD_CONTRACT:
        raise ValueError("COLLECTOR_SOURCE_WORKLOAD_ASSESSMENT_UPGRADE_REQUIRED")
    base = f"/v1/projects/{project}/native-request-profiles/analyses"
    if analysis_id is not None:
        candidates = [str(UUID(str(analysis_id)))]
    else:
        page = control("GET", base + "?limit=100&offset=0")
        if type(page) is not list or len(page) > 100:
            raise ValueError("CACHE_WORKLOAD_BOUNDED_ANALYSIS_PAGE_REQUIRED")
        candidates = [
            str(UUID(str(row["id"])))
            for row in page
            if (
                isinstance(row, dict)
                and row.get("workflow") == profile.binding.workflow_id
                and row.get("environment") == environment
                and row.get("state") == "COMPLETED"
                and isinstance(row.get("summary"), dict)
                and row["summary"].get("cache_trial_eligible") is True
            )
        ][:5]
    receipts = []
    for identifier in candidates:
        receipt = CacheWorkloadAssessment.model_validate(
            control(
                "POST",
                base + f"/{identifier}/cache-trial",
                body,
            )
        )
        if (
            str(receipt.project_id) != project
            or receipt.environment != environment
            or receipt.workflow != profile.binding.workflow_id
            or str(receipt.analysis_id) != identifier
            or receipt.source_binding_sha256 != fingerprint(body["source_binding"])
            or receipt.cache_execution_sha256 != fingerprint(body["cache_execution"])
            or receipt.baseline_model != body["baseline_model"]
        ):
            raise ValueError("CACHE_WORKLOAD_RESPONSE_SCOPE_MISMATCH")
        if datetime.fromisoformat(receipt.valid_until) <= datetime.now(UTC):
            raise ValueError("CACHE_WORKLOAD_RESPONSE_EXPIRED")
        receipts.append(receipt.model_dump(mode="json"))
        if receipt.decision == "prepare_separate_cache_trial":
            break
    chosen = next(
        (row for row in receipts if row["decision"] == "prepare_separate_cache_trial"), None
    )
    result = {
        "version": CACHE_WORKLOAD_CONTRACT,
        "project_id": project,
        "environment": environment,
        "workflow_id": profile.binding.workflow_id,
        "source_binding_sha256": fingerprint(body["source_binding"]),
        "cache_execution_sha256": fingerprint(body["cache_execution"]),
        "decision": "prepare_separate_cache_trial" if chosen else "abstain",
        "reasons": []
        if chosen
        else sorted({reason for row in receipts for reason in row["reasons"]})
        if receipts
        else ["NO_RETAINED_ELIGIBLE_REQUEST_PROFILE_ANALYSIS"],
        "selected_assessment": chosen,
        "considered_assessments": receipts,
        "provider_calls": 0,
        "encrypted_storage_reads": 0,
        "paid_calls_authorized": False,
        "source_change_authorized": False,
    }
    with workspace_lock(directory):
        current = load_session(root)
        if (current["id"], current["analysis_sha256"], current["revision"]) != original:
            raise ValueError("WORKFLOW_CHANGED_DURING_WORKLOAD_ASSESSMENT")
        if clean_source(root) != source:
            raise ValueError("SOURCE_CHANGED_DURING_WORKLOAD_ASSESSMENT")
        current_profile = selected_profile(root, current, profile.binding.workflow_id)
        if current_profile.binding != profile.binding:
            raise ValueError("SOURCE_CHANGED_DURING_WORKLOAD_ASSESSMENT")
        history = current.get("workload_assessments", [])
        current["workload_assessments"] = [*history[-4:], result]
        save_session(directory, current)
    return result
