"""Read-only source-policy workload attribution; never an execution approval."""

from datetime import UTC, datetime
from uuid import UUID

from pydantic import TypeAdapter

from cutback.core.cache_workload import (
    POLICY_WORKLOAD_CONTRACT,
    CacheWorkloadAssessment,
    PolicyWorkloadBinding,
)
from cutback.core.exact_cache import ExactCacheExecution
from cutback.core.models import Identifier
from cutback.core.request_profiles import fingerprint


def source_input(root, *, producer_file, cache_execution):
    from cutback.policy_source import inspect_configuration
    from cutback.scanner import scan

    policy, source = inspect_configuration(root, producer_file)
    if source.async_consumer or policy.cache_execution is not None or policy.strategy.routes:
        raise ValueError("POLICY_WORKLOAD_UNCACHED_SYNC_NATIVE_REFERENCE_REQUIRED")
    if len(source.commit) != 40:
        raise ValueError("POLICY_WORKLOAD_SHA1_ATTRIBUTION_REQUIRED")
    inventory = scan(root)
    sites = [
        site
        for site in inventory["call_sites"]
        if site["source_file"] == source.producer_file
        and "cutback_policy_native_wrapper" in site["evidence"]
    ]
    if len(sites) != 1:
        raise ValueError("POLICY_WORKLOAD_EXACT_NATIVE_CALLSITE_REQUIRED")
    binding = PolicyWorkloadBinding(source=source, call_site_id=sites[0]["call_site_id"])
    contract = ExactCacheExecution.model_validate(cache_execution)
    return {
        "source_binding": binding.model_dump(mode="json"),
        "baseline_model": policy.strategy.model,
        "cache_execution": contract.model_dump(mode="json"),
    }, source


def collect(
    root,
    control,
    project,
    *,
    producer_file,
    cache_execution,
    environment="staging",
    analysis_id=None,
):
    project = str(UUID(str(project)))
    environment = TypeAdapter(Identifier).validate_python(environment)
    body, source = source_input(root, producer_file=producer_file, cache_execution=cache_execution)
    if (
        control("GET", "/v1/health/ready").get("source_policy_workload_contract")
        != POLICY_WORKLOAD_CONTRACT
    ):
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
            if isinstance(row, dict)
            and row.get("workflow") == source.workflow
            and row.get("environment") == environment
            and row.get("state") == "COMPLETED"
            and isinstance(row.get("summary"), dict)
            and row["summary"].get("cache_trial_eligible") is True
        ][:5]
    receipts = []
    for identifier in candidates:
        receipt = CacheWorkloadAssessment.model_validate(
            control("POST", base + f"/{identifier}/source-policy-cache-trial", body)
        )
        if (
            str(receipt.project_id) != project
            or receipt.environment != environment
            or receipt.workflow != source.workflow
            or str(receipt.analysis_id) != identifier
            or receipt.source_binding_sha256 != fingerprint(body["source_binding"])
            or receipt.baseline_model != body["baseline_model"]
            or receipt.cache_execution_sha256 != fingerprint(body["cache_execution"])
            or datetime.fromisoformat(receipt.valid_until) <= datetime.now(UTC)
        ):
            raise ValueError("POLICY_WORKLOAD_CURRENT_SCOPED_ASSESSMENT_REQUIRED")
        receipts.append(receipt.model_dump(mode="json"))
        if receipt.decision == "prepare_separate_cache_trial":
            break
    chosen = next(
        (row for row in receipts if row["decision"] == "prepare_separate_cache_trial"), None
    )
    result = {
        "version": POLICY_WORKLOAD_CONTRACT,
        "project_id": project,
        "environment": environment,
        "workflow": source.workflow,
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
        "next_action": "cutback source-policy prepare"
        if chosen
        else "inspect source-matched profile coverage and current rights",
        "provider_calls": 0,
        "encrypted_storage_reads": 0,
        "paid_calls_authorized": False,
        "source_change_authorized": False,
        "production_hit_rate": None,
        "realized_savings_usd": None,
    }
    if source_input(root, producer_file=producer_file, cache_execution=cache_execution) != (
        body,
        source,
    ):
        raise ValueError("POLICY_WORKLOAD_SOURCE_CHANGED_DURING_ASSESSMENT")
    return result


def assess(
    root,
    control,
    project,
    *,
    producer_file,
    cache_execution,
    environment="staging",
    analysis_id=None,
):
    from cutback.agent import workspace
    from cutback.core.workspace import workspace_lock, write_private

    root, directory = workspace(root)
    with workspace_lock(directory):
        result = collect(
            root,
            control,
            project,
            producer_file=producer_file,
            cache_execution=cache_execution,
            environment=environment,
            analysis_id=analysis_id,
        )
        write_private(directory / "policy-workload.json", result)
    return result
