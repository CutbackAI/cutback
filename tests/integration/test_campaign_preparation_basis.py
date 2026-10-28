import json
from uuid import UUID

import httpx
import pytest
from sqlalchemy import func, select
from support_agent import prepare_fresh
from test_agent_campaign import baseline

from cutback.agent import advance, prepare
from cutback.agent_campaign import prepare as prepare_campaign
from cutback.server.db import CapturePolicyRecord, ExperimentRunRecord

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("kind", ["classifier", "messages"])
def test_campaign_basis_supports_cheapest_model_minimum_cap_and_resumes_same_policy(
    api, tenant, tmp_path, monkeypatch, kind
):
    root, control, state, args = prepare_fresh(
        api,
        tenant,
        tmp_path,
        monkeypatch,
        kind,
        for_campaign=True,
        campaign_generator_provider="openai" if kind == "messages" else None,
        model="gpt-4.1-mini" if kind == "classifier" else None,
        output_cap=16 if kind == "classifier" else None,
    )
    assert state["phase"] == "PLAN_READY" and state["search_mode"] == "campaign_basis"
    assert state["next_action"]["code"] == "PREPARE_BOUNDED_CAMPAIGN"
    assert state["next_action"]["provider_calls"] == 0
    with pytest.raises(ValueError, match="CAMPAIGN_BASIS_REQUIRES_CAMPAIGN"):
        advance(root, control, plan_hash=state["plan"]["plan_hash"], approval_ref="not-static")
    # Explicit vendor authorization persists for this unchanged frozen preparation.
    args["campaign_generator_provider"] = None
    resumed = prepare(root, control, tenant[0], **args)
    assert (
        resumed["plan"]["id"] == state["plan"]["id"] and resumed["policy_id"] == state["policy_id"]
    )
    row = prepare_campaign(
        root,
        control,
        baseline_snapshot_id=baseline(api, tenant, state["selected_workflow"]),
        max_usd="10",
        max_requests=1000,
        savings_per_success="0.01",
        forecast_successful_tasks=10000,
    )
    assert row["status"] == "READY_FOR_APPROVAL"
    assert (
        row["economic_envelope"]["static_baseline_kind"]
        == "unchanged_control_no_cheaper_static_variant"
    )
    with api[2].state.sessions() as session:
        policy = session.get(CapturePolicyRecord, UUID(state["policy_id"]))
        assert policy.policy["allowed_providers"] == (
            ["anthropic", "openai"] if kind == "messages" else ["openai"]
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(ExperimentRunRecord)
                .where(ExperimentRunRecord.project_id == UUID(tenant[0]))
            )
            == 0
        )


def test_campaign_extra_generator_vendor_is_required_before_reading_examples_or_upload(
    api, tenant, tmp_path, monkeypatch
):
    def forbidden(*args, **kwargs):
        raise AssertionError("EXAMPLES_MUST_NOT_BE_READ_WITHOUT_GENERATOR_VENDOR_SCOPE")

    monkeypatch.setattr("cutback.evals.records.read_records", forbidden)
    with pytest.raises(ValueError, match="EXPLICIT_OPENAI_GENERATOR_VENDOR_AUTHORIZATION_REQUIRED"):
        prepare_fresh(api, tenant, tmp_path, monkeypatch, "messages", for_campaign=True)
    with api[2].state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(CapturePolicyRecord)
                .where(CapturePolicyRecord.project_id == UUID(tenant[0]))
            )
            == 0
        )


def test_cheapest_minimum_cap_application_completes_instruction_only_native_campaign(
    api, tenant, tmp_path, monkeypatch
):
    from support_agent import native_response
    from test_campaign_workflow import advance as tick
    from test_campaign_workflow import child

    from cutback.agent_campaign import advance, proposal, status
    from cutback.evals.executors import OpenAIExecutor
    from cutback.server.execution_db import ExecutionCall
    from cutback.server.proposal_worker import run_proposal_one
    from cutback.server.worker import run_one

    root, control, state, _ = prepare_fresh(
        api,
        tenant,
        tmp_path,
        monkeypatch,
        for_campaign=True,
        model="gpt-4.1-mini",
        output_cap=16,
    )
    original = (root / "app.py").read_bytes()
    row = prepare_campaign(
        root,
        control,
        baseline_snapshot_id=baseline(api, tenant, state["selected_workflow"]),
        max_usd="10",
        max_requests=1000,
        savings_per_success="0.01",
        forecast_successful_tasks=10000,
    )
    assert row["instruction_edit_qualified"]
    advance(root, control, campaign_hash=row["campaign_hash"], approval_ref="synthetic-only")
    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "synthetic-unused-key")
    requests = []

    def transport(request):
        assert request.url.host == "api.openai.com" and request.url.path == "/v1/responses"
        body = json.loads(request.content)
        requests.append(body)
        assert body["model"] == "gpt-4.1-mini" and body["store"] is False
        reply = native_response(request).json()
        if "text" in body:
            assert body["text"]["format"]["strict"] is True
            scope = json.loads(body["input"])
            assert len(scope["seeds"]) == 2
            assert {seed["model"] for seed in scope["seeds"]} == {"gpt-4.1-mini"}
            reply["output"][0]["content"][0]["text"] = json.dumps(
                {
                    "proposals": [
                        {
                            "seed_key": scope["seeds"][0]["key"],
                            "instruction": "Return A only.",
                            "max_output_tokens": 16,
                        }
                    ]
                }
            )
            input_tokens, output_tokens = 100, 80
        else:
            assert body["max_output_tokens"] == 16
            input_tokens = 20 if body["instructions"] == "Return A only." else 100
            output_tokens = 1
        reply["usage"].update(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        )
        return httpx.Response(200, json=reply)

    executor = OpenAIExecutor(http_client=httpx.Client(transport=httpx.MockTransport(transport)))
    executor.synthetic_transport = True
    store = api[2].state.artifact_store
    try:
        assert tick(api, store, row)["status"] == "WAITING_PROPOSAL"
        assert (
            run_proposal_one(
                api[2].state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=child(api, row, "proposal"),
            )["status"]
            == "COMPLETED"
        )
        assert tick(api, store, row)["status"] == "WAITING_VALIDATION"
        for phase, expected in (
            ("validation", "WAITING_CONFIRMATION"),
            ("confirmation", "COMPLETED"),
        ):
            result = run_one(
                api[2].state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=child(api, row, phase),
            )
            assert result["status"] == "COMPLETED", result
            assert tick(api, store, row)["status"] == expected
        current = status(root, control)
        assert current["requests"] == len(requests) and current["requests"] > 1
        with api[2].state.sessions() as session:
            assert session.scalar(
                select(func.count())
                .select_from(ExecutionCall)
                .where(ExecutionCall.project_id == UUID(tenant[0]))
            ) == len(requests)
        with pytest.raises(ValueError, match="INDEPENDENT_CONFIRMATION_GATE_REQUIRED"):
            proposal(root, control, purpose="synthetic-review")
        artifact = proposal(root, control, purpose="synthetic-review", sample_only=True)
        assert "Return A only." in artifact["diff"]
        assert "Owned synthetic classification task" in artifact["rollback_diff"]
        assert artifact["confirmation_gate"]["passed"]
        assert (root / "app.py").read_bytes() == original
        before = len(requests)
        assert (
            proposal(root, control, purpose="repeat", sample_only=True)["diff_sha256"]
            == (artifact["diff_sha256"])
        )
        assert len(requests) == before
    finally:
        executor.close()
