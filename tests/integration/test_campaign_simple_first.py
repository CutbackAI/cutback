"""Native transports and PostgreSQL exercise the versioned adaptive search order."""

import json
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import select
from test_campaign_workflow import advance, approve, child, native, prepare

from cutback.server.campaign_db import CampaignRecord, CampaignRun
from cutback.server.db import ExperimentRunRecord
from cutback.server.execution_db import ExecutionCall
from cutback.server.lineage_db import DatasetExposure
from cutback.server.proposal_worker import run_proposal_one
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


def evaluate(api, store, row, executor, phase):
    result = run_one(
        api[2].state.sessions,
        store,
        executor=executor,
        execution_enabled=True,
        only_run_id=child(api, row, phase),
    )
    assert result["status"] == "COMPLETED", result


@pytest.mark.parametrize("static_worse", [False, True])
def test_simple_first_skips_generation_or_falls_back_under_one_parent(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    static_worse,
):
    base, store, row, request = prepare(
        api, tenant, tmp_path, monkeypatch, version="cutback.improvement-campaign.v2"
    )
    assert row["economic_envelope"]["contract"] == "cutback.campaign-budget.v2"
    assert row["economic_envelope"]["instruction_search_enabled"]
    assert api[0].post(base + "/campaigns", headers=api[1], json=request).json()["id"] == row["id"]
    approve(api, base, row)
    executor, requests = native(monkeypatch, static_worse=static_worse)
    sessions = api[2].state.sessions
    try:
        assert advance(api, store, row)["status"] == "WAITING_VALIDATION"
        static_id = child(api, row, "validation")
        assert child(api, row, "proposal") is None
        assert advance(api, store, row)["status"] == "WAITING_VALIDATION"
        assert not requests
        evaluate(api, store, row, executor, "validation")
        static_calls = len(requests)
        assert static_calls == row["economic_envelope"]["initial_validation_requests"]
        assert not any("text" in r for r in requests)
        with sessions() as session:
            assert (
                session.scalar(
                    select(DatasetExposure.id).where(
                        DatasetExposure.source_ref.startswith(f"campaign:{row['id']}:")
                    )
                )
                is None
            )
        expected = "WAITING_PROPOSAL" if static_worse else "WAITING_CONFIRMATION"
        assert advance(api, store, row)["status"] == expected
        if static_worse:
            proposal_id = child(api, row, "proposal")
            assert (
                run_proposal_one(
                    sessions,
                    store,
                    executor=executor,
                    execution_enabled=True,
                    only_run_id=proposal_id,
                )["status"]
                == "COMPLETED"
            )
            scope = json.loads(requests[-1]["input"])
            feedback = scope["validation_aggregate_feedback"]
            assert len(feedback) == 1 and not feedback[0]["quality_gate_passed"]
            assert "validation" not in json.dumps(scope["training"]).lower()
            assert advance(api, store, row)["status"] == "WAITING_VALIDATION"
            assert child(api, row, "validation") != static_id
            evaluate(api, store, row, executor, "validation")
            assert advance(api, store, row)["status"] == "WAITING_CONFIRMATION"
        evaluate(api, store, row, executor, "confirmation")
        assert advance(api, store, row)["status"] == "COMPLETED"
        detail = api[0].get(base + f"/campaigns/{row['id']}", headers=tenant[2]).json()
        assert len(detail["runs"]) == (4 if static_worse else 2)
        assert detail["requests"] == detail["known_cost_requests"] == len(requests)
        assert detail["billing_complete"]
        assert detail["summary"]["sample_only_gate"]["passed"]
        assert not detail["summary"]["deployment_authorized"]
        assert detail["summary"]["production_savings_usd"] is None
        assert "Return A only." not in json.dumps(detail)
        assert sum("text" in r for r in requests) == int(static_worse)
        with sessions() as session:
            parent = session.get(CampaignRecord, UUID(row["id"]))
            calls = session.scalars(
                select(ExecutionCall)
                .join(CampaignRun, CampaignRun.run_id == ExecutionCall.run_id)
                .where(CampaignRun.campaign_id == parent.id)
            ).all()
            assert len(calls) == parent.requests_used <= parent.economic_envelope["max_requests"]
            assert parent.spent_usd == sum(c.spent_usd for c in calls)
            assert parent.reserved_usd <= Decimal(parent.economic_envelope["effective_max_usd"])
            assert len({c.id for c in calls}) == len(calls)
        before = len(requests)
        assert advance(api, store, row)["status"] == "IDLE"
        assert len(requests) == before
    finally:
        executor.close()


@pytest.mark.parametrize("failed", [False, True])
def test_static_only_envelope_confirms_or_rejects_without_a_proposer(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    failed,
):
    # Forty fixture families: eight validation and eight test, two targets per group.
    base, store, row, _ = prepare(
        api,
        tenant,
        tmp_path,
        monkeypatch,
        version="cutback.improvement-campaign.v2",
        budget={"max_usd": "10", "max_requests": 32, "max_wall_seconds": 300},
    )
    assert row["status"] == "READY_FOR_APPROVAL"
    envelope = row["economic_envelope"]
    assert envelope["rounds"] == envelope["variants_per_round"] == 0
    assert envelope["generator_input_upper_bytes"] is None
    approve(api, base, row)
    executor, requests = native(monkeypatch, cheaper_worse=failed)
    try:
        assert advance(api, store, row)["status"] == "WAITING_VALIDATION"
        evaluate(api, store, row, executor, "validation")
        assert advance(api, store, row)["status"] == (
            "COMPLETED" if failed else "WAITING_CONFIRMATION"
        )
        if not failed:
            evaluate(api, store, row, executor, "confirmation")
            assert advance(api, store, row)["status"] == "COMPLETED"
        assert child(api, row, "proposal") is None
        assert not any("text" in r for r in requests)
        assert len(requests) <= 32
        detail = api[0].get(base + f"/campaigns/{row['id']}", headers=api[1]).json()
        assert detail["billing_complete"]
        if failed:
            assert (
                detail["summary"]["search_decision"]
                == "static_alternative_failed_no_generation_budget"
            )
            assert (
                detail["summary"]["decision"] == "reject_no_quality_preserving_lower_cost_candidate"
            )
    finally:
        executor.close()


def test_modern_proposer_is_only_called_after_static_rejection_with_its_exact_contract(
    api,
    tenant,
    tmp_path,
    monkeypatch,
):
    from cutback.core.native_models import NativeModelContract
    from cutback.demo_campaigns import synthetic_executor

    contract = NativeModelContract.current_luna_text()
    base, store, row, _ = prepare(
        api,
        tenant,
        tmp_path,
        monkeypatch,
        version="cutback.improvement-campaign.v2",
        generator={
            "transport": "scripted_test_only",
            "variants_per_round": 2,
            "model": {
                "key": "proposer",
                "provider": "openai",
                "model": "gpt-6-luna",
                "max_input_tokens": 24000,
                "max_output_tokens": 2048,
                "native_contract": contract.model_dump(mode="json"),
            },
        },
    )
    approve(api, base, row)
    executor, requests = synthetic_executor(seeds_from_scope=True)
    sessions = api[2].state.sessions
    try:
        assert advance(api, store, row)["status"] == "WAITING_VALIDATION"
        evaluate(api, store, row, executor, "validation")
        assert not any("text" in request for request in requests)
        assert advance(api, store, row)["status"] == "WAITING_PROPOSAL"
        assert (
            run_proposal_one(
                sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=child(api, row, "proposal"),
            )["status"]
            == "COMPLETED"
        )
        proposal = requests[-1]
        assert proposal["model"] == "gpt-6-luna"
        assert proposal["reasoning"] == {"effort": "none"}
        assert proposal["service_tier"] == "default"
        assert advance(api, store, row)["status"] == "WAITING_VALIDATION"
        evaluate(api, store, row, executor, "validation")
        assert advance(api, store, row)["status"] == "WAITING_CONFIRMATION"
        evaluate(api, store, row, executor, "confirmation")
        assert advance(api, store, row)["status"] == "COMPLETED"
        with sessions() as session:
            calls = session.scalars(
                select(ExecutionCall)
                .join(CampaignRun, CampaignRun.run_id == ExecutionCall.run_id)
                .where(
                    CampaignRun.campaign_id == UUID(row["id"]),
                    ExecutionCall.purpose == "optimization",
                )
            ).all()
            assert len(calls) == 1 and calls[0].price_sha256 == contract.price_sha256
        detail = api[0].get(base + f"/campaigns/{row['id']}", headers=api[1]).json()
        assert detail["billing_complete"] and detail["requests"] == len(requests)
    finally:
        executor.close()


def test_failed_static_confirmation_never_reopens_search_on_consumed_test_groups(
    api,
    tenant,
    tmp_path,
    monkeypatch,
):
    base, store, row, _ = prepare(
        api, tenant, tmp_path, monkeypatch, version="cutback.improvement-campaign.v2"
    )
    approve(api, base, row)
    good, validation_requests = native(monkeypatch)
    bad, confirmation_requests = native(monkeypatch, cheaper_worse=True)
    try:
        assert advance(api, store, row)["status"] == "WAITING_VALIDATION"
        evaluate(api, store, row, good, "validation")
        assert advance(api, store, row)["status"] == "WAITING_CONFIRMATION"
        evaluate(api, store, row, bad, "confirmation")
        assert advance(api, store, row)["status"] == "COMPLETED"
        detail = api[0].get(base + f"/campaigns/{row['id']}", headers=api[1]).json()
        assert detail["summary"]["decision"] == "confirmation_requires_review_or_rejected"
        assert not detail["summary"]["sample_only_gate"]["passed"]
        assert len(detail["runs"]) == 2 and child(api, row, "proposal") is None
        assert detail["requests"] == len(validation_requests) + len(confirmation_requests)
        assert advance(api, store, row)["status"] == "IDLE"
    finally:
        good.close()
        bad.close()


def test_static_transport_outage_stops_parent_without_generation_or_replay(
    api,
    tenant,
    tmp_path,
    monkeypatch,
):
    base, store, row, _ = prepare(
        api, tenant, tmp_path, monkeypatch, version="cutback.improvement-campaign.v2"
    )
    approve(api, base, row)
    executor, requests = native(monkeypatch, fail=True)
    try:
        assert advance(api, store, row)["status"] == "WAITING_VALIDATION"
        run_id = child(api, row, "validation")
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
