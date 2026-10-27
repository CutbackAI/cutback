"""Real PostgreSQL and pinned native SDK, synthetic transport; no paid requests."""

import json

import httpx
import pytest
from support_agent import native_response, prepare_fresh

from cutback.agent import advance, confirm, gate, load_session, proposal
from cutback.evals.executors import OpenAIExecutor
from cutback.server.worker import run_one


@pytest.mark.postgres
def test_source_bound_search_rejects_quality_loss_and_prepares_actual_winning_diff(
    api,
    tenant,
    tmp_path,
    monkeypatch,
):
    root, control, state, args = prepare_fresh(api, tenant, tmp_path, monkeypatch, search=True)
    assert state["search_mode"] == "model_and_output_cap"
    plan = state["plan"]
    assert plan["requests"] == plan["sample_n"] * 6
    before = (root / "app.py").read_bytes()
    seen = []

    def transport(request):
        body = json.loads(request.content)
        seen.append(body)
        response = native_response(request).json()
        cap = body["max_output_tokens"]
        if cap == 16:
            response["output"][0]["content"][0]["text"] = "B"
        response["usage"].update(output_tokens=2 if cap <= 32 else 16)
        response["usage"]["total_tokens"] = 20 + response["usage"]["output_tokens"]
        return httpx.Response(200, json=response)

    assert not seen
    queued = advance(root, control, plan_hash=plan["plan_hash"], approval_ref="synthetic-search")
    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "synthetic-never-provider")
    executor = OpenAIExecutor(http_client=httpx.Client(transport=httpx.MockTransport(transport)))
    try:
        outcome = run_one(
            api[2].state.sessions,
            api[2].state.artifact_store,
            executor=executor,
            execution_enabled=True,
            only_run_id=queued["run_id"],
        )
    finally:
        executor.close()
    assert outcome["status"] == "COMPLETED", outcome
    assert len(seen) == plan["requests"]
    assert {b["max_output_tokens"] for b in seen} == {16, 32, 64}
    complete = advance(root, control)
    rows = complete["search_results"]["candidates"]
    assert len(rows) == 6
    assert all(not row["eligible"] for row in rows if row["max_output_tokens"] == 16)
    assert complete["search_results"]["production_savings_usd"] is None
    winner = next(
        row for row in rows if row["model"] == "gpt-4.1-mini" and row["max_output_tokens"] == 32
    )
    assert winner["eligible"]
    assert not gate(root, control, sample_only=True)["passed"]
    with pytest.raises(ValueError, match="SEARCH_REQUIRES_INDEPENDENT_CONFIRMATION"):
        proposal(root, control, purpose="premature-search-patch", candidate=winner["key"])
    assert complete["next_action"]["code"] == "SELECT_CANDIDATE_AND_FREEZE_CONFIRMATION"
    with pytest.raises(ValueError, match="CONFIRMATION_EXCEEDS_BUDGET"):
        confirm(root, control, candidate=winner["key"], max_usd="0", purpose="no-budget")
    assert load_session(root)["phase"] == "COMPLETED"
    confirmation = confirm(
        root,
        control,
        candidate=winner["key"],
        max_usd="5",
        max_requests=1000,
        purpose="freeze-owned-confirmation",
    )
    assert confirmation["phase"] == "PLAN_READY"
    assert confirmation["plan"]["split"] == "test"
    search_calls = len(seen)
    assert search_calls == plan["requests"]
    base = f"/v1/projects/{tenant[0]}"
    frozen = control(
        "POST",
        base + f"/experiments/plans/{confirmation['plan']['id']}/content",
        {"purpose": "owned-contract-check"},
    )["specification"]
    assert len(frozen["candidates"]) == 1 and frozen["candidates"][0]["key"] == winner["key"]
    altered = json.loads(json.dumps(frozen))
    altered["candidates"][0]["max_output_tokens"] = 48
    with pytest.raises(ValueError, match="SELECTED_SEARCH_CANDIDATE_CHANGED"):
        control(
            "POST",
            base + "/experiments/plans",
            {"dataset_id": state["dataset_id"], "specification": altered},
        )
    altered = json.loads(json.dumps(frozen))
    altered["metric"]["minimum"] = 0.6
    with pytest.raises(ValueError, match="CONFIRMATION_CONTRACT_CHANGED"):
        control(
            "POST",
            base + "/experiments/plans",
            {"dataset_id": state["dataset_id"], "specification": altered},
        )
    confirmed = advance(
        root,
        control,
        plan_hash=confirmation["plan"]["plan_hash"],
        approval_ref="owned-separate-confirmation",
    )
    with pytest.raises(ValueError, match="TEST_GROUPS_ALREADY_RESERVED_OR_CONSUMED"):
        control(
            "POST",
            base + "/experiments/plans",
            {"dataset_id": state["dataset_id"], "specification": frozen},
        )
    executor = OpenAIExecutor(http_client=httpx.Client(transport=httpx.MockTransport(transport)))
    try:
        result = run_one(
            api[2].state.sessions,
            api[2].state.artifact_store,
            executor=executor,
            execution_enabled=True,
            only_run_id=confirmed["run_id"],
        )
    finally:
        executor.close()
    assert result["status"] == "COMPLETED", result
    assert len(seen) - search_calls == confirmation["plan"]["requests"]
    validation_inputs = {b["input"] for b in seen[:search_calls]}
    test_inputs = {b["input"] for b in seen[search_calls:]}
    assert validation_inputs and test_inputs and not validation_inputs & test_inputs
    assert advance(root, control)["phase"] == "COMPLETED"
    assert gate(root, control, candidate=winner["key"], sample_only=True)["passed"]
    assert not gate(root, control, candidate=winner["key"])["passed"]
    from typer.testing import CliRunner

    from cutback.cli import app

    monkeypatch.setattr("cutback.agent_control.connect", lambda target: (control, tenant[0]))
    runner = CliRunner()
    sample = runner.invoke(app, ["workflow", "gate", "--target", str(root), "--sample-only"])
    assert sample.exit_code == 0 and json.loads(sample.stdout)["scope"] == "sample_only"
    strict = runner.invoke(app, ["workflow", "gate", "--target", str(root)])
    assert strict.exit_code == 1 and not json.loads(strict.stdout)["passed"]
    artifact = proposal(root, control, purpose="owned-search-review", candidate=winner["key"])
    assert artifact["transformations"] == ["model", "output_cap"]
    assert "gpt-4.1-mini" in artifact["diff"] and "max_output_tokens=32" in artifact["diff"]
    assert "max_output_tokens=64" in artifact["rollback_diff"]
    assert (root / "app.py").read_bytes() == before
    metadata = json.dumps(load_session(root))
    assert "Owned synthetic classification task" not in metadata
    assert "Owned synthetic AA topic" not in metadata
    assert "synthetic-never-provider" not in metadata
    with pytest.raises(ValueError, match="NO_SOURCE_PATCH_CANDIDATE_PASSED_GATES"):
        proposal(
            root,
            control,
            purpose="rejected-truncation",
            candidate=next(r["key"] for r in rows if r["max_output_tokens"] == 16),
        )


@pytest.mark.postgres
def test_truncated_native_search_preserves_uncertain_billing_and_never_retries(
    api,
    tenant,
    tmp_path,
    monkeypatch,
):
    root, control, state, _ = prepare_fresh(api, tenant, tmp_path, monkeypatch, search=True)
    calls = []

    def transport(request):
        calls.append(1)
        response = native_response(request).json()
        response["status"] = "incomplete"
        response["incomplete_details"] = {"reason": "max_output_tokens"}
        return httpx.Response(200, json=response)

    queued = advance(
        root, control, plan_hash=state["plan"]["plan_hash"], approval_ref="synthetic-stop"
    )
    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "synthetic-never-provider")
    executor = OpenAIExecutor(http_client=httpx.Client(transport=httpx.MockTransport(transport)))
    try:
        outcome = run_one(
            api[2].state.sessions,
            api[2].state.artifact_store,
            executor=executor,
            execution_enabled=True,
            only_run_id=queued["run_id"],
        )
    finally:
        executor.close()
    assert outcome["status"] == "FAILED" and len(calls) == 1
    assert outcome["results"]["spent_modeled_usd"] is None
    assert not outcome["results"]["recommendations"]
    stopped = advance(root, control)
    assert stopped["phase"] == "STOPPED"
    assert not stopped["search_results"]["eligible_candidates"]
    assert not advance(root, control)["next_action"]["automatic_billable_retry"]
    assert len(calls) == 1


@pytest.mark.postgres
def test_concurrent_confirmation_queues_cannot_consume_test_groups_twice(
    api,
    tenant,
    tmp_path,
    monkeypatch,
):
    from concurrent.futures import ThreadPoolExecutor

    root, control, state, _ = prepare_fresh(api, tenant, tmp_path, monkeypatch, search=True)
    queued = advance(
        root, control, plan_hash=state["plan"]["plan_hash"], approval_ref="owned-search"
    )
    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "synthetic-never-provider")
    executor = OpenAIExecutor(
        http_client=httpx.Client(transport=httpx.MockTransport(native_response))
    )
    try:
        assert (
            run_one(
                api[2].state.sessions,
                api[2].state.artifact_store,
                executor=executor,
                execution_enabled=True,
                only_run_id=queued["run_id"],
            )["status"]
            == "COMPLETED"
        )
    finally:
        executor.close()
    complete = advance(root, control)
    candidate = next(
        r["candidate"]["key"]
        for r in complete["decisions"]
        if r["quality_gate_passed"] and r["cheaper_on_sample"]
    )
    ready = confirm(
        root,
        control,
        candidate=candidate,
        max_usd="5",
        max_requests=1000,
        purpose="owned-queue-race",
    )
    base = f"/v1/projects/{tenant[0]}"
    spec = control(
        "POST",
        base + f"/experiments/plans/{ready['plan']['id']}/content",
        {"purpose": "owned-second-plan"},
    )["specification"]
    spec["budget"]["max_usd"] = "6"
    second = control(
        "POST",
        base + "/experiments/plans",
        {"dataset_id": state["dataset_id"], "specification": spec},
    )
    plans = [ready["plan"], second]
    for plan in plans:
        control(
            "POST",
            base + f"/experiments/plans/{plan['id']}/approve",
            {"plan_hash": plan["plan_hash"], "approval_ref": "owned-race-budget"},
        )

    def queue(plan):
        response = api[0].post(base + "/experiments", headers=api[1], json={"plan_id": plan["id"]})
        return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(queue, plans))
    assert sorted(status for status, _ in results) == [202, 409]
    rejected = next(body for status, body in results if status == 409)
    assert rejected["code"] == "CONFIRMATION_TEST_GROUPS_ALREADY_RESERVED_OR_CONSUMED"
    accepted = next(body for status, body in results if status == 202)
    control("POST", base + f"/experiments/{accepted['id']}/cancel", {})
