"""Independent cached-current program fixtures; all observations and model transports synthetic."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from integration.test_policy_activity_scope import event
from integration.test_source_policy_execution import prepared
from integration.test_task_outcomes import declaration, environment, ingest, spans

from cutback.core.improvement_policy import SourceImprovementPolicy
from cutback.core.policy_activity import PolicyRuntimeSummary
from cutback.core.policy_execution import (
    PolicyExecutionRef,
    SourcePolicyExecutionSpec,
    uncached_shadow_reference,
)
from cutback.core.policy_runtime_reference import freeze_reference
from cutback.evals.experiments import ExperimentPlan
from cutback.evals.policy_models import policy_descriptors


def cached_program(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    *,
    requested=True,
    current_routed=False,
    source_binding=None,
    repeat_cases=True,
    cache_execution=None,
    cache_controls=False,
    call_site_id=None,
    **changes,
):
    base, store, dataset, policy_id, executor, calls, old, _, imports = prepared(
        api,
        tenant,
        tmp_path,
        monkeypatch,
        cached=False,
        routed=False,
        current_routed=current_routed,
    )
    if repeat_cases:
        cases = []
        for family in range(120):
            for repeat in range(2):
                cases.append(
                    {
                        "id": f"owned-current-{family:03d}-{repeat}",
                        "group": f"owned-current-family-{family:03d}",
                        "input": "Owned current replay case "
                        + chr(65 + family // 26)
                        + chr(65 + family % 26)
                        + (" short" if family % 2 else " long" * 25),
                        "gold": "A",
                    }
                )
        response = api[0].post(
            base + "/datasets",
            headers=api[1],
            json={
                "workflow": old.workflow,
                "policy_id": policy_id,
                "source_type": "synthetic_fixture",
                "provenance_ref": "owned-current-cache-replay",
                "independent_labels": True,
                "cases": cases,
            },
        )
        assert response.status_code == 201, response.text
        dataset = response.json()
    raw = imports[0]["policy"]
    raw = {
        **raw,
        "cache_execution": cache_execution
        or {
            "freshness_ref": "owned-current-source-cache",
            "ttl_seconds": 60,
            "content_retention_authorized": True,
            "immutable_or_safely_invalidated": True,
            "operation_cost_upper_usd": "0.000001",
            "operation_cost_basis_ref": "owned-synthetic",
        },
    }
    original = SourceImprovementPolicy.model_validate(raw)
    candidates = {
        entry["key"]: SourceImprovementPolicy.model_validate(entry["policy"])
        for entry in imports[1:]
    }
    if cache_controls:
        candidates = {
            key: value.model_copy(update={"cache_execution": original.cache_execution})
            for key, value in candidates.items()
        }
    source = source_binding or old.source_policy_execution.source.model_copy(
        update={"policy_sha256": original.policy_sha256}
    )
    assert source.policy_sha256 == original.policy_sha256
    policies = {"reference": original, **candidates}
    references = []
    for key, value in policies.items():
        response = api[0].post(
            base + "/source-policies",
            headers=api[1],
            json={
                "request_id": str(uuid4()),
                "dataset_id": dataset["id"],
                "source": source.model_dump(mode="json"),
                "key": key,
                "policy": value.model_dump(mode="json"),
                "purpose": "owned-cached-current-fixture",
            },
        )
        assert response.status_code == 201, response.text
        references.append(PolicyExecutionRef.model_validate(response.json()))
    references = [
        (
            value.model_copy(update={"cache_requested": True})
            if requested
            else uncached_shadow_reference(value)
        )
        if value.cache_enabled
        else value
        for value in references
    ]
    execution = SourcePolicyExecutionSpec(
        version="cutback.source-policy-execution.v2",
        source=source,
        reference=references[0],
        candidates=references[1:],
        transport="scripted_test_only",
    )
    models = policy_descriptors(execution, policies)
    template = ExperimentPlan.model_validate(
        {
            **old.model_dump(mode="json"),
            "dataset_hash": dataset["immutable_hash"],
            "baseline": models["reference"].model_dump(mode="json"),
            "candidates": [models[row.key].model_dump(mode="json") for row in references[1:]],
            "source_policy_execution": execution.model_dump(mode="json"),
        }
    )
    result = api[0].post(
        base + "/experiments/plans",
        headers=api[1],
        json={"dataset_id": dataset["id"], "specification": template.model_dump(mode="json")},
    )
    assert result.status_code == 201, result.text
    template_id = result.json()["id"]
    now = datetime.now(UTC)
    started = int((now - timedelta(seconds=2)).timestamp()) * 1_000_000_000
    body = event(
        **({"call_site_id": call_site_id} if call_site_id is not None else {}),
        environment="staging",
        service_name="owned-cached-current",
        workflow=source.workflow,
        source_commit=source.commit,
        policy_sha256=source.policy_sha256,
        started_ns=started,
        ended_ns=started + 1000,
        cache_operation_upper_usd=str(original.cache_execution.operation_cost_upper_usd),
        cache_cost_basis_ref=original.cache_execution.operation_cost_basis_ref,
    )
    if not requested:
        body.update(
            cache_requested=False,
            state="inactive",
            native_sdk_invocations=1,
            cache_operations=0,
            cache_write="not_applicable",
            usage_origin="native_response",
        )
    response = api[0].post(
        "/v1/ingest/policy-runtime",
        headers=tenant[1],
        json={"project_id": tenant[0], "events": [body]},
    )
    assert response.status_code == 200, response.text
    scope = dict(
        environment="staging",
        service_name="owned-cached-current",
        workflow=source.workflow,
        call_site_id=body["call_site_id"],
        source_commit=source.commit,
        policy_sha256=source.policy_sha256,
        window_start=(now - timedelta(minutes=10)).isoformat(),
        window_end=now.isoformat(),
    )
    response = api[0].post(base + "/policy-runtime/inspect", headers=tenant[2], json=scope)
    assert response.status_code == 200, response.text
    runtime = freeze_reference(source, PolicyRuntimeSummary.model_validate(response.json()))
    task = declaration(
        workflow=template.workflow,
        components=[{"key": "owned-compute", "kind": "compute", "amount_usd": "1"}],
    )
    spans(api, tenant, task)
    assert ingest(api, tenant, task).status_code == 200
    response = api[0].post(
        base + "/task-cost-snapshots",
        headers=api[1],
        params={"environment_id": environment(api, tenant[0])},
        json={
            "snapshot_id": str(uuid4()),
            "workflow": template.workflow,
            "expected_tasks": 1,
            "start": (now - timedelta(days=1)).isoformat(),
            "end": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 200, response.text
    request = {
        "request_id": str(uuid4()),
        "template_plan_id": template_id,
        "baseline_snapshot_id": response.json()["id"],
        "budget": {"max_usd": "100", "max_requests": 1000, "max_wall_seconds": 600},
        "conservative_savings_per_success_usd": "0.01",
        "forecast_successful_tasks": 100000,
        "version": "cutback.source-policy-program.v2",
        "runtime_reference": runtime.model_dump(mode="json"),
        **changes,
    }
    response = api[0].post(base + "/source-policy-programs", headers=api[1], json=request)
    assert response.status_code == 201, response.text
    return (
        base,
        store,
        dataset,
        policy_id,
        executor,
        calls,
        template,
        response.json(),
        request,
        body,
    )
