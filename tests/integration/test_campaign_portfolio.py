"""One frozen portfolio, one parent ledger, native transports and real PostgreSQL."""

import json
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from support_evaluation import dataset_body, plan_for
from test_campaign_simple_first import evaluate
from test_campaign_workflow import advance, approve, child, native, prepare

from cutback.evals.campaigns import automatic_static_portfolio
from cutback.evals.experiments import ExperimentPlan
from cutback.server.campaign_db import CampaignRecord, CampaignRun
from cutback.server.db import CapturePolicyRecord, ExperimentRunRecord
from cutback.server.execution_db import ExecutionCall
from cutback.server.proposal_worker import run_proposal_one
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


def ready(api, tenant, tmp_path, monkeypatch, **changes):
    legacy = plan_for({"immutable_hash": "a" * 64}).model_dump(mode="json")
    legacy["baseline"] = {
        "key": "baseline",
        "provider": "openai",
        "model": "gpt-4.1",
        "instruction": "Return A.",
        "max_output_tokens": 256,
    }
    legacy["candidates"] = [
        {
            **legacy["baseline"],
            "key": "simple-downgrade",
            "model": "gpt-4.1-mini",
        }
    ]
    legacy["approved_providers"] = ["openai"]
    models = automatic_static_portfolio(ExperimentPlan.model_validate(legacy))
    return prepare(
        api,
        tenant,
        tmp_path,
        monkeypatch,
        version="cutback.improvement-campaign.v3",
        static_portfolio=[m.model_dump(mode="json") for m in models],
        **changes,
    )


@pytest.mark.parametrize("static_worse", [False, True])
def test_native_portfolio_evaluates_all_then_confirms_or_uses_only_reserved_fallback(
    api, tenant, tmp_path, monkeypatch, static_worse
):
    base, store, row, request = ready(api, tenant, tmp_path, monkeypatch)
    assert row["economic_envelope"]["contract"] == "cutback.campaign-budget.v3"
    assert len(row["economic_envelope"]["static_portfolio"]) == 4
    assert row["economic_envelope"]["instruction_search_enabled"]
    assert api[0].post(base + "/campaigns", headers=api[1], json=request).json()["id"] == row["id"]
    assert api[0].post(base + "/campaigns", headers=tenant[2], json=request).status_code == 403
    approve(api, base, row)
    executor, requests = native(monkeypatch, static_worse=static_worse)
    try:
        assert advance(api, store, row)["status"] == "WAITING_VALIDATION"
        assert advance(api, store, row)["status"] == "WAITING_VALIDATION"
        assert not requests
        evaluate(api, store, row, executor, "validation")
        assert len(requests) == row["economic_envelope"]["initial_validation_requests"]
        assert len({(r["model"], r["max_output_tokens"]) for r in requests}) == 5
        assert child(api, row, "proposal") is None
        assert advance(api, store, row)["status"] == (
            "WAITING_PROPOSAL" if static_worse else "WAITING_CONFIRMATION"
        )
        if static_worse:
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
            scope = json.loads(requests[-1]["input"])
            assert len(scope["seeds"]) == 5
            assert len(scope["validation_aggregate_feedback"]) == 4
            assert not any(f["quality_gate_passed"] for f in scope["validation_aggregate_feedback"])
            assert advance(api, store, row)["status"] == "WAITING_VALIDATION"
            evaluate(api, store, row, executor, "validation")
            assert advance(api, store, row)["status"] == "WAITING_CONFIRMATION"
        evaluate(api, store, row, executor, "confirmation")
        assert advance(api, store, row)["status"] == "COMPLETED"
        detail = api[0].get(base + f"/campaigns/{row['id']}", headers=tenant[2]).json()
        assert "Return A." not in json.dumps(detail)
        assert detail["billing_complete"]
        assert detail["requests"] == len(requests) <= row["economic_envelope"]["max_requests"]
        assert len(detail["candidates"]) == 4 + int(static_worse)
        assert detail["summary"]["decision"] == "synthetic_confirmation_passed_real_data_required"
        with api[2].state.sessions() as session:
            parent = session.get(CampaignRecord, UUID(row["id"]))
            calls = session.scalars(
                select(ExecutionCall)
                .join(CampaignRun, CampaignRun.run_id == ExecutionCall.run_id)
                .where(CampaignRun.campaign_id == parent.id)
            ).all()
            assert len(calls) == parent.requests_used
            assert parent.spent_usd == sum((c.spent_usd for c in calls), Decimal(0))
            assert parent.reserved_usd <= Decimal(parent.economic_envelope["effective_max_usd"])
        before = len(requests)
        assert advance(api, store, row)["status"] == "IDLE"
        assert len(requests) == before
    finally:
        executor.close()


def test_portfolio_uncertain_native_outage_keeps_hold_and_never_replays_or_generates(
    api, tenant, tmp_path, monkeypatch
):
    base, store, row, _ = ready(api, tenant, tmp_path, monkeypatch)
    approve(api, base, row)
    assert advance(api, store, row)["status"] == "WAITING_VALIDATION"
    executor, requests = native(monkeypatch, fail=True)
    run_id = child(api, row, "validation")
    try:
        result = run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=run_id,
        )
        assert result["status"] == "FAILED"
        assert advance(api, store, row)["status"] == "FAILED"
        assert child(api, row, "proposal") is None
        with api[2].state.sessions() as session:
            run = session.get(ExperimentRunRecord, run_id)
            assert run.requests_used == 1 and run.known_cost_requests == 0
            call = session.scalar(select(ExecutionCall).where(ExecutionCall.run_id == run_id))
            assert call.reserved_usd > 0 and call.spent_usd is None
        assert (
            run_one(
                api[2].state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=run_id,
            )["status"]
            == "IDLE"
        )
        assert len(requests) == 1
    finally:
        executor.close()


@pytest.mark.parametrize("failed", [False, True])
def test_preparation_narrows_portfolio_before_approval_without_shrinking_heldout_samples(
    api, tenant, tmp_path, monkeypatch, failed
):
    base, store, row, request = ready(
        api,
        tenant,
        tmp_path,
        monkeypatch,
        budget={"max_usd": "10", "max_requests": 40, "max_wall_seconds": 300},
    )
    assert row["status"] == "READY_FOR_APPROVAL"
    envelope = row["economic_envelope"]
    assert envelope["requested_static_count"] == 4
    assert len(envelope["selected_static_keys"]) == 2
    assert len(envelope["omitted_static_keys"]) == 2
    assert len(row["specification"]["static_portfolio"]) == 2
    assert envelope["initial_validation_requests"] == 24
    assert envelope["max_requests"] == 40 and envelope["rounds"] == 0
    assert api[0].post(base + "/campaigns", headers=api[1], json=request).json()["id"] == row["id"]
    approve(api, base, row)
    executor, requests = native(monkeypatch, static_worse=failed)
    try:
        assert advance(api, store, row)["status"] == "WAITING_VALIDATION"
        evaluate(api, store, row, executor, "validation")
        assert len(requests) == 24
        assert advance(api, store, row)["status"] == (
            "COMPLETED" if failed else "WAITING_CONFIRMATION"
        )
        if not failed:
            evaluate(api, store, row, executor, "confirmation")
            assert advance(api, store, row)["status"] == "COMPLETED"
        assert len(requests) == (24 if failed else 40)
        assert child(api, row, "proposal") is None
    finally:
        executor.close()


def test_portfolio_cancellation_after_dispatch_records_late_bill_and_stops_new_calls(
    api, tenant, tmp_path, monkeypatch
):
    base, store, row, _ = ready(api, tenant, tmp_path, monkeypatch)
    approve(api, base, row)
    assert advance(api, store, row)["status"] == "WAITING_VALIDATION"
    acknowledgements = []

    def cancel():
        acknowledgements.append(
            api[0]
            .post(base + f"/campaigns/{row['id']}/cancel", headers=api[1], json={})
            .status_code
        )

    executor, requests = native(monkeypatch, cancel=cancel)
    try:
        run_id = child(api, row, "validation")
        run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=run_id,
        )
        assert acknowledgements == [200]
        detail = api[0].get(base + f"/campaigns/{row['id']}", headers=api[1]).json()
        assert detail["status"] == "CANCELLED"
        assert detail["requests"] == detail["known_cost_requests"] == len(requests) == 1
        assert Decimal(detail["known_spend_subtotal_usd"]) > 0
        assert advance(api, store, row)["status"] == "IDLE"
        assert (
            run_one(
                api[2].state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=run_id,
            )["status"]
            == "IDLE"
        )
        assert len(requests) == 1
    finally:
        executor.close()


def test_anthropic_portfolio_preserves_native_messages_and_confirms_without_openai_dispatch(
    api, tenant, tmp_path, monkeypatch
):
    import httpx2

    from cutback.evals.executors import AnthropicExecutor

    base, store, _, request = ready(api, tenant, tmp_path, monkeypatch)
    client, admin, app = api
    with app.state.sessions() as session:
        original = session.scalar(
            select(CapturePolicyRecord).where(CapturePolicyRecord.project_id == UUID(tenant[0]))
        )
        policy = dict(original.policy)
    permitted = client.post(
        base + "/capture-policies",
        headers=admin,
        json={
            **policy,
            "allowed_providers": ["anthropic", "openai"],
        },
    )
    assert permitted.status_code == 201, permitted.text
    data = dataset_body(40).model_dump(mode="json")
    data["policy_id"] = permitted.json()["id"]
    imported = client.post(base + "/datasets", headers=admin, json=data)
    assert imported.status_code == 201, imported.text
    spec = request["specification"]
    spec.update(dataset_id=imported.json()["id"])
    template = spec["template"]
    template["dataset_hash"] = imported.json()["immutable_hash"]
    template["approved_providers"] = ["anthropic", "openai"]
    template["baseline"].update(provider="anthropic", model="claude-sonnet-4-6")
    template["candidates"][0].update(provider="anthropic", model="claude-haiku-4-5")
    spec["static_portfolio"] = [
        m.model_dump(mode="json")
        for m in automatic_static_portfolio(ExperimentPlan.model_validate(template))
    ]
    request["request_id"] = str(uuid4())
    prepared = client.post(base + "/campaigns", headers=admin, json=request)
    assert prepared.status_code == 201, prepared.text
    row = prepared.json()
    approve(api, base, row)
    requests = []

    def handle(native_request):
        assert str(native_request.url) == "https://api.anthropic.com/v1/messages"
        body = json.loads(native_request.content)
        assert body["system"] == "Return A."
        assert body["messages"][0]["role"] == "user"
        assert set(body) == {"model", "max_tokens", "messages", "system"}
        with app.state.sessions() as session:
            parent = session.get(CampaignRecord, UUID(row["id"]))
            assert parent.requests_used == len(requests) + 1
            assert parent.reserved_usd > 0
        requests.append(body)
        return httpx2.Response(
            200,
            json={
                "id": "msg_owned",
                "type": "message",
                "role": "assistant",
                "model": body["model"],
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "A"}],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50 if body["model"] == "claude-sonnet-4-6" else 4,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            },
        )

    monkeypatch.setenv("CUTBACK_EXPERIMENT_ANTHROPIC_KEY", "synthetic-no-provider-key")
    monkeypatch.setattr(
        "cutback.evals.executors.anthropic_transport",
        lambda: httpx2.Client(
            transport=httpx2.MockTransport(handle), trust_env=False, follow_redirects=False
        ),
    )
    executor = AnthropicExecutor()
    executor.synthetic_transport = True
    try:
        assert advance(api, store, row)["status"] == "WAITING_VALIDATION"
        evaluate(api, store, row, executor, "validation")
        assert advance(api, store, row)["status"] == "WAITING_CONFIRMATION"
        assert child(api, row, "proposal") is None
        evaluate(api, store, row, executor, "confirmation")
        assert advance(api, store, row)["status"] == "COMPLETED"
        report = client.get(base + f"/campaigns/{row['id']}", headers=admin).json()
        assert report["billing_complete"] and report["requests"] == len(requests)
    finally:
        executor.close()
