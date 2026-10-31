"""Agent cache portfolios and fresh held-out confirmation, without paid inference."""

from uuid import UUID

import httpx
import pytest
from support_agent import native_response, prepare_fresh

from cutback.agent import advance, confirm, load_session, prepare, proposal
from cutback.evals.executors import OpenAIExecutor
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


def cache_contract():
    return {
        "freshness_ref": "owned-immutable-task-v1",
        "ttl_seconds": 300,
        "content_retention_authorized": True,
        "immutable_or_safely_invalidated": True,
        "operation_cost_upper_usd": "0.000000001",
        "operation_cost_basis_ref": "owned-synthetic-operation-bound",
    }


@pytest.mark.parametrize("automatic", [False, True])
def test_agent_cache_search_and_confirmation_use_separate_cold_namespaces(
    api, tenant, tmp_path, monkeypatch, automatic
):
    root, control, state, options = prepare_fresh(
        api,
        tenant,
        tmp_path,
        monkeypatch,
        search=not automatic,
        automatic=automatic,
        model="gpt-4.1-mini",
        output_cap=16,
        min_test_samples=6,
        fixture_transport=True,
        exact_cache_contract=cache_contract(),
    )
    content = control(
        "POST",
        f"/v1/projects/{tenant[0]}/experiments/plans/{state['plan']['id']}/content",
        {"purpose": "owned-cache-plan-check"},
    )["specification"]
    assert content["repeats"] == 2 and len(content["candidates"]) == 1
    selected = content["candidates"][0]["key"]
    assert content["candidates"][0]["exact_cache"]["freshness_ref"] == "owned-immutable-task-v1"
    assert (
        prepare(root, control, tenant[0], **options)["plan"]["plan_hash"]
        == state["plan"]["plan_hash"]
    )
    monkeypatch.setenv("CUTBACK_CACHE_ENABLED", "true")
    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "owned-synthetic-only")
    calls = []

    def handle(request):
        calls.append(request)
        body = native_response(request).json()
        body.update(parallel_tool_calls=False, tool_choice="none", tools=[])
        return httpx.Response(200, json=body)

    executor = OpenAIExecutor(
        http_client=httpx.Client(transport=httpx.MockTransport(handle), trust_env=False)
    )
    executor.synthetic_transport = True
    try:
        queued = advance(
            root,
            control,
            plan_hash=state["plan"]["plan_hash"],
            approval_ref="owned-cache-validation",
        )
        result = run_one(
            api[2].state.sessions,
            api[2].state.artifact_store,
            executor=executor,
            execution_enabled=True,
            only_run_id=UUID(queued["run_id"]),
        )
        assert result["status"] == "COMPLETED", result
        first = result["results"]["models"][1]["exact_cache"]
        assert first["execution_qualified"] and first["cache_hits"] > 0
        assert len(calls) == 3 * first["cache_hits"]
        finished = advance(root, control)
        assert finished["search_results"]["eligible_candidates"] == 1
        confirmation = confirm(
            root,
            control,
            candidate=selected,
            max_usd="5",
            max_requests=1000,
            purpose="owned-unused-cache-confirmation",
        )
        before = len(calls)
        queued = advance(
            root,
            control,
            plan_hash=confirmation["plan"]["plan_hash"],
            approval_ref="owned-cache-confirmation",
        )
        result = run_one(
            api[2].state.sessions,
            api[2].state.artifact_store,
            executor=executor,
            execution_enabled=True,
            only_run_id=UUID(queued["run_id"]),
        )
        assert result["status"] == "COMPLETED", result
        second = result["results"]["models"][1]["exact_cache"]
        assert second["execution_qualified"] and len(calls) - before == 3 * second["cache_hits"]
        completed = advance(root, control)
        assert completed["phase"] == "COMPLETED"
        assert load_session(root)["confirmation"]["search_run_id"] != queued["run_id"]
        assert not executor.exact_cache_ready
        before_source = (root / "app.py").read_bytes()
        artifact = proposal(
            root, control, purpose="owned-confirmed-cache-source-review", candidate=selected
        )
        assert artifact["transformations"] == ["exact_cache"]
        assert artifact["runtime_cache"]["credentials_in_source"] is False
        assert "CUTBACK_NATIVE_CACHE_SCOPE_FILE" in artifact["diff"]
        assert artifact["rollback_diff"] and artifact["runtime_cache"]["workflow"]
        assert (root / "app.py").read_bytes() == before_source
        if not automatic:
            from support_exact_cache import qualify_source_runtime

            qualify_source_runtime(
                api,
                tenant,
                root,
                artifact,
                load_session(root),
                cache_contract(),
                tmp_path / "owned-cache-source-review",
                monkeypatch,
            )
    finally:
        executor.close()


def test_old_collector_cache_contract_is_refused_before_any_upload(
    api, tenant, tmp_path, monkeypatch
):
    from support_agent import fresh_application

    from cutback.agent import analyze
    from cutback.agent_control import Control

    root = tmp_path / "old-collector-cache-application"
    root.mkdir()
    fresh_application(root, model="gpt-4.1-mini", output_cap=16)
    analyze(root)
    control = Control("http://127.0.0.1", api[1]["Authorization"][7:], client=api[0])
    options = {
        "files": ["cases.jsonl"],
        "rights_basis": "synthetic",
        "rights_ref": "owned-cache-protocol-check",
        "max_usd": "5",
        "max_requests": 1000,
        "search": True,
        "minimum": 0.7,
        "independent_labels": True,
    }
    writes = []

    class OldCollector:
        endpoint = control.endpoint

        def __call__(self, method, path, body=None):
            if path == "/v1/health/ready":
                ready = control(method, path, body)
                ready.pop("exact_cache_contract", None)
                return ready
            writes.append(path)
            raise AssertionError("CACHE_INCOMPATIBLE_COLLECTOR_MUST_NOT_RECEIVE_UPLOADS")

    with pytest.raises(ValueError, match="COLLECTOR_EXACT_CACHE_UPGRADE_REQUIRED"):
        prepare(
            root, OldCollector(), tenant[0], **{**options, "exact_cache_contract": cache_contract()}
        )
    assert not writes
