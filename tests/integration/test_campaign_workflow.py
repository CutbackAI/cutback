"""Owned native SDK transports, PostgreSQL and the complete bounded campaign journey."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select
from support_evaluation import authorized_dataset
from test_task_outcomes import declaration, environment, ingest, spans

from cutback.evals.executors import OpenAIExecutor
from cutback.server.campaign_db import (
    CampaignCandidate,
    CampaignRecord,
    CampaignRun,
    CampaignSelection,
)
from cutback.server.campaign_worker import advance_one
from cutback.server.execution_db import ExecutionCall
from cutback.server.proposal_worker import run_proposal_one
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


def prepare(api, tenant, tmp_path, monkeypatch, **changes):
    base, store, dataset, _ = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    task = declaration(
        workflow="classify",
        components=[{"key": "synthetic-compute", "kind": "compute", "amount_usd": "1"}],
    )
    spans(api, tenant, task)
    assert ingest(api, tenant, task).status_code == 200
    response = api[0].post(
        base + "/task-cost-snapshots",
        headers=api[1],
        params={"environment_id": environment(api, tenant[0])},
        json={
            "snapshot_id": str(uuid4()),
            "start": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "end": datetime.now(UTC).isoformat(),
            "workflow": "classify",
            "expected_tasks": 1,
        },
    )
    assert response.status_code == 200, response.text
    value = {
        "dataset_id": dataset["id"],
        "baseline_snapshot_id": response.json()["id"],
        "environment": "staging",
        "template": {
            "workflow": "classify",
            "dataset_hash": dataset["immutable_hash"],
            "split": "validation",
            "baseline": {
                "key": "baseline",
                "provider": "openai",
                "model": "gpt-4.1",
                "instruction": "Return A.",
                "max_output_tokens": 256,
            },
            "candidates": [
                {
                    "key": "simple-downgrade",
                    "provider": "openai",
                    "model": "gpt-4.1-mini",
                    "instruction": "Return A.",
                    "max_output_tokens": 256,
                }
            ],
            "metric": {
                "kind": "exact_label",
                "minimum": 1,
                "min_test_samples": 3,
                "min_test_groups": 3,
            },
            "budget": {"max_usd": "10", "max_requests": 500},
            "approved_providers": ["openai"],
        },
        "generator": {
            "model": {
                "key": "proposer",
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "max_input_tokens": 20000,
                "max_output_tokens": 512,
            },
            "transport": "scripted_test_only",
            "variants_per_round": 1,
        },
        "budget": {"max_usd": "10", "max_requests": 500, "max_wall_seconds": 300},
        "conservative_savings_per_success_usd": "0.01",
        "forecast_successful_tasks": 10000,
        **changes,
    }
    request = {"request_id": str(uuid4()), "specification": value}
    response = api[0].post(base + "/campaigns", headers=api[1], json=request)
    assert response.status_code == 201, response.text
    return base, store, response.json(), request


def approve(api, base, row):
    result = api[0].post(
        base + f"/campaigns/{row['id']}/approve",
        headers=api[1],
        json={"campaign_hash": row["campaign_hash"], "approval_ref": "synthetic-only"},
    )
    assert result.status_code == 200, result.text


def advance(api, store, row, *, synthetic=True):
    with api[2].state.sessions.begin() as session:
        session.get(CampaignRecord, UUID(row["id"])).available_at = None
    return advance_one(
        api[2].state.sessions,
        store,
        execution_enabled=True,
        only_campaign_id=row["id"],
        synthetic_transport=synthetic,
    )


def child(api, row, phase):
    with api[2].state.sessions() as session:
        return session.scalar(
            select(CampaignRun.run_id)
            .where(CampaignRun.campaign_id == UUID(row["id"]), CampaignRun.phase == phase)
            .order_by(CampaignRun.round_index.desc())
            .limit(1)
        )


def native(
    monkeypatch,
    *,
    fail=False,
    malformed=False,
    cheaper_worse=False,
    cancel=None,
    repeat=False,
    dynamic_seeds=False,
):
    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "synthetic-unused-key")
    requests = []

    def transport(request):
        assert request.url.host == "api.openai.com" and request.url.path == "/v1/responses"
        body = json.loads(request.content)
        assert body["store"] is False
        requests.append(body)
        if fail:
            raise httpx.ReadTimeout("synthetic-secret-must-stay-private", request=request)
        proposal = "text" in body
        if proposal:
            assert body["text"]["format"]["strict"] is True
            output = (
                "bad-json"
                if malformed
                else json.dumps(
                    {
                        "proposals": [
                            {
                                "seed_key": "simple-downgrade",
                                "instruction": "Return A only."
                                if repeat
                                else f"Return A only. Variant {len(requests)}.",
                                "max_output_tokens": 64,
                            }
                        ]
                    }
                )
            )
            if dynamic_seeds:
                scope = json.loads(body["input"])
                output = json.dumps(
                    {
                        "proposals": [
                            {
                                "seed_key": seed["key"],
                                "instruction": "Return A only.",
                                "max_output_tokens": 32,
                            }
                            for seed in scope["seeds"]
                        ]
                    }
                )
        else:
            output = "B" if cheaper_worse and body["model"] != "gpt-4.1" else "A"
        if cancel:
            cancel()
        output_tokens = (
            80
            if proposal
            else 50
            if body["model"] == "gpt-4.1"
            else 4
            if body["max_output_tokens"] == 64
            else 20
        )
        return httpx.Response(
            200,
            json={
                "id": "resp_synthetic_" + uuid4().hex,
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": body["model"],
                "output": [
                    {
                        "type": "message",
                        "id": "msg_synthetic",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": output, "annotations": []}],
                    }
                ],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": output_tokens,
                    "total_tokens": 100 + output_tokens,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    executor = OpenAIExecutor(http_client=httpx.Client(transport=httpx.MockTransport(transport)))
    executor.synthetic_transport = True
    return executor, requests


def test_native_campaign_generates_freezes_selects_and_confirms(api, tenant, tmp_path, monkeypatch):
    base, store, row, request = prepare(api, tenant, tmp_path, monkeypatch)
    assert row["status"] == "READY_FOR_APPROVAL"
    assert api[0].post(base + "/campaigns", headers=api[1], json=request).json()["id"] == row["id"]
    assert (
        api[0]
        .post(
            base + f"/campaigns/{row['id']}/approve",
            headers=tenant[2],
            json={"campaign_hash": row["campaign_hash"], "approval_ref": "denied"},
        )
        .status_code
        == 403
    )
    approve(api, base, row)
    executor, requests = native(monkeypatch)
    sessions = api[2].state.sessions
    try:
        assert advance(api, store, row)["status"] == "WAITING_PROPOSAL"
        proposal_id = child(api, row, "proposal")
        assert (
            run_proposal_one(
                sessions, store, executor=executor, execution_enabled=True, only_run_id=proposal_id
            )["status"]
            == "COMPLETED"
        )
        assert len(requests) == 1
        training = json.loads(requests[0]["input"])
        assert "validation_aggregate_feedback" in training
        assert len(training["training"]) <= 8
        assert advance(api, store, row)["status"] == "WAITING_VALIDATION"
        validation_id = child(api, row, "validation")
        result = run_one(
            sessions, store, executor=executor, execution_enabled=True, only_run_id=validation_id
        )
        assert result["status"] == "COMPLETED", result
        transition = advance(api, store, row)
        assert transition["status"] == "WAITING_CONFIRMATION", (
            api[0].get(base + f"/campaigns/{row['id']}", headers=api[1]).json()["summary"]
        )
        confirmation_id = child(api, row, "confirmation")
        result = run_one(
            sessions, store, executor=executor, execution_enabled=True, only_run_id=confirmation_id
        )
        assert result["status"] == "COMPLETED", result
        assert advance(api, store, row)["status"] == "COMPLETED"
        detail = api[0].get(base + f"/campaigns/{row['id']}", headers=tenant[2])
        assert detail.status_code == 200, detail.text
        evidence = detail.json()
        assert len(evidence["runs"]) == 3 and len(evidence["candidates"]) == 2
        assert evidence["requests"] == evidence["known_cost_requests"] == len(requests)
        assert evidence["billing_complete"]
        assert not evidence["summary"]["deployment_authorized"]
        assert evidence["summary"]["production_savings_usd"] is None
        assert "Return A only" not in detail.text and "content_nonce" not in json.dumps(
            evidence["summary"]
        )
        with sessions() as session:
            calls = session.scalars(
                select(ExecutionCall)
                .join(CampaignRun, CampaignRun.run_id == ExecutionCall.run_id)
                .where(CampaignRun.campaign_id == UUID(row["id"]))
            ).all()
            assert len(calls) == len(requests)
            assert Decimal(evidence["known_spend_subtotal_usd"]) == sum(c.spent_usd for c in calls)
            assert sum(c.purpose == "optimization" for c in calls) == 1
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(CampaignSelection)
                    .where(CampaignSelection.campaign_id == UUID(row["id"]))
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(CampaignCandidate)
                    .where(CampaignCandidate.campaign_id == UUID(row["id"]))
                )
                == 2
            )
        count = len(requests)
        assert (
            run_proposal_one(
                sessions, store, executor=executor, execution_enabled=True, only_run_id=proposal_id
            )["status"]
            == "IDLE"
        )
        assert len(requests) == count
    finally:
        executor.close()


@pytest.mark.parametrize("malformed", [False, True])
def test_uncertain_or_malformed_proposer_never_replays(
    api, tenant, tmp_path, monkeypatch, malformed
):
    base, store, row, _ = prepare(api, tenant, tmp_path, monkeypatch)
    approve(api, base, row)
    assert advance(api, store, row)["status"] == "WAITING_PROPOSAL"
    executor, requests = native(monkeypatch, fail=not malformed, malformed=malformed)
    try:
        run_id = child(api, row, "proposal")
        assert (
            run_proposal_one(
                api[2].state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=run_id,
            )["status"]
            == "FAILED"
        )
        assert len(requests) == 1
        assert advance(api, store, row)["status"] == "FAILED"
        assert (
            run_proposal_one(
                api[2].state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=run_id,
            )["status"]
            == "IDLE"
        )
        assert len(requests) == 1
        detail = api[0].get(base + f"/campaigns/{row['id']}", headers=api[1]).json()
        assert detail["requests"] == 1 and detail["known_cost_requests"] == int(malformed)
        assert detail["billing_complete"] == malformed
    finally:
        executor.close()


def test_unknown_opportunity_abstains_without_children(api, tenant, tmp_path, monkeypatch):
    base, store, row, _ = prepare(
        api, tenant, tmp_path, monkeypatch, forecast_successful_tasks=None
    )
    assert row["status"] == "ABSTAINED" and row["requests"] == 0
    response = api[0].post(
        base + f"/campaigns/{row['id']}/approve",
        headers=api[1],
        json={"campaign_hash": row["campaign_hash"], "approval_ref": "not-permitted"},
    )
    assert response.status_code == 409
    assert advance(api, store, row)["status"] == "IDLE"


def test_cancelled_proposer_repairs_late_cost_without_restarting(
    api, tenant, tmp_path, monkeypatch
):
    base, store, row, _ = prepare(api, tenant, tmp_path, monkeypatch)
    approve(api, base, row)
    assert advance(api, store, row)["status"] == "WAITING_PROPOSAL"

    cancelled = []

    def cancel():
        response = api[0].post(base + f"/campaigns/{row['id']}/cancel", headers=api[1], json={})
        cancelled.append((response.status_code, response.text))

    executor, requests = native(monkeypatch, cancel=cancel)
    try:
        run_proposal_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=child(api, row, "proposal"),
        )
        report = api[0].get(base + f"/campaigns/{row['id']}", headers=api[1]).json()
        assert cancelled and cancelled[0][0] == 200, cancelled
        assert report["status"] == "CANCELLED"
        assert report["requests"] == report["known_cost_requests"] == len(requests) == 1
        assert Decimal(report["known_spend_subtotal_usd"]) > 0
        assert advance(api, store, row)["status"] == "IDLE"
    finally:
        executor.close()


def test_current_policy_revocation_stops_derived_target_before_transport(
    api, tenant, tmp_path, monkeypatch
):
    from cutback.server.db import CapturePolicyRecord

    base, store, row, _ = prepare(api, tenant, tmp_path, monkeypatch)
    approve(api, base, row)
    assert advance(api, store, row)["status"] == "WAITING_PROPOSAL"
    executor, requests = native(monkeypatch)
    try:
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
        assert advance(api, store, row)["status"] == "WAITING_VALIDATION"
        with api[2].state.sessions.begin() as session:
            parent = session.get(CampaignRecord, UUID(row["id"]))
            session.get(CapturePolicyRecord, parent.policy_id).enabled = False
        result = run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=child(api, row, "validation"),
        )
        assert result["status"] == "FAILED" and len(requests) == 1
        assert advance(api, store, row)["status"] == "FAILED"
    finally:
        executor.close()


def test_cheap_but_worse_campaign_does_not_consume_confirmation(api, tenant, tmp_path, monkeypatch):
    base, store, row, _ = prepare(api, tenant, tmp_path, monkeypatch)
    approve(api, base, row)
    executor, requests = native(monkeypatch, cheaper_worse=True)
    try:
        assert advance(api, store, row)["status"] == "WAITING_PROPOSAL"
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
        assert advance(api, store, row)["status"] == "WAITING_VALIDATION"
        assert (
            run_one(
                api[2].state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=child(api, row, "validation"),
            )["status"]
            == "COMPLETED"
        )
        assert advance(api, store, row)["status"] == "COMPLETED"
        detail = api[0].get(base + f"/campaigns/{row['id']}", headers=api[1]).json()
        assert detail["summary"]["decision"] == "reject_no_quality_preserving_lower_cost_candidate"
        assert len(detail["runs"]) == 2 and child(api, row, "confirmation") is None
        assert detail["requests"] == len(requests)
    finally:
        executor.close()


def test_two_rounds_send_only_training_and_bounded_validation_feedback(
    api, tenant, tmp_path, monkeypatch
):
    base, store, _, request = prepare(api, tenant, tmp_path, monkeypatch)
    request["request_id"] = str(uuid4())
    request["specification"]["generator"]["rounds"] = 2
    response = api[0].post(base + "/campaigns", headers=api[1], json=request)
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["economic_envelope"]["rounds"] == 2
    approve(api, base, row)
    executor, requests = native(monkeypatch)
    try:
        assert advance(api, store, row)["status"] == "WAITING_PROPOSAL"
        for index in range(2):
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
            training = json.loads(next(r for r in reversed(requests) if "text" in r)["input"])
            feedback = training["validation_aggregate_feedback"]
            assert bool(feedback) == bool(index)
            prior = training["previous_candidate_versions"]
            assert len(prior) == index
            if index:
                assert prior[0]["key"] == "generated-r1-1"
                assert prior[0]["instruction"].startswith("Return A only. Variant")
            assert all(
                set(f)
                == {
                    "candidate_key",
                    "quality_gate_passed",
                    "sample_metric_min",
                    "cost_complete",
                    "sample_cost_per_request_usd",
                }
                for f in feedback
            )
            assert all(set(t) == {"input", "gold"} for t in training["training"])
            assert advance(api, store, row)["status"] == "WAITING_VALIDATION"
            assert (
                run_one(
                    api[2].state.sessions,
                    store,
                    executor=executor,
                    execution_enabled=True,
                    only_run_id=child(api, row, "validation"),
                )["status"]
                == "COMPLETED"
            )
            assert advance(api, store, row)["status"] == (
                "WAITING_PROPOSAL" if index == 0 else "WAITING_CONFIRMATION"
            )
        assert (
            run_one(
                api[2].state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=child(api, row, "confirmation"),
            )["status"]
            == "COMPLETED"
        )
        assert advance(api, store, row)["status"] == "COMPLETED"
        detail = api[0].get(base + f"/campaigns/{row['id']}", headers=api[1]).json()
        assert len(detail["runs"]) == 5 and len(detail["candidates"]) == 3
        assert detail["requests"] == detail["known_cost_requests"] == len(requests)
    finally:
        executor.close()


def test_duplicate_round_stops_search_keeps_versions_and_charges_all_known_overhead(
    api, tenant, tmp_path, monkeypatch
):
    from cutback.server.improvement_api import collect_run_costs

    base, store, _, request = prepare(api, tenant, tmp_path, monkeypatch)
    request["request_id"] = str(uuid4())
    request["specification"]["generator"]["rounds"] = 2
    response = api[0].post(base + "/campaigns", headers=api[1], json=request)
    assert response.status_code == 201, response.text
    row = response.json()
    approve(api, base, row)
    sessions = api[2].state.sessions
    executor, requests = native(monkeypatch, repeat=True)
    try:
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
        assert advance(api, store, row)["status"] == "WAITING_VALIDATION"
        assert (
            run_one(
                sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=child(api, row, "validation"),
            )["status"]
            == "COMPLETED"
        )
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
        assert advance(api, store, row)["status"] == "WAITING_CONFIRMATION"
        assert (
            run_one(
                sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=child(api, row, "confirmation"),
            )["status"]
            == "COMPLETED"
        )
        assert advance(api, store, row)["status"] == "COMPLETED"
        report = api[0].get(base + f"/campaigns/{row['id']}", headers=api[1]).json()
        assert len(report["runs"]) == 4 and len(report["candidates"]) == 3
        assert sum(r["phase"] == "validation" for r in report["runs"]) == 1
        assert report["candidates"][-1]["disposition"] == "duplicate_not_evaluated"
        assert report["summary"]["stopping_reason"] == "duplicate_proposals"
        assert report["requests"] == report["known_cost_requests"] == len(requests)
        all_ids = [UUID(r["run_id"]) for r in report["runs"]]
        confirmation = UUID(
            next(r["run_id"] for r in report["runs"] if r["phase"] == "confirmation")
        )
        with sessions() as session:
            partial, missing = collect_run_costs(session, UUID(tenant[0]), [confirmation], None)
            assert len(partial) == 1 and missing == 3
            full, missing = collect_run_costs(session, UUID(tenant[0]), all_ids, None)
            assert missing == 0 and len(full) == 4
            assert sum(r.known_cost_subtotal_usd for r in full) == Decimal(
                report["known_spend_subtotal_usd"]
            )
        from cutback.server.campaign_retention import retain_campaigns

        with sessions.begin() as session:
            old = datetime.now(UTC) - timedelta(days=400)
            parent = session.get(CampaignRecord, UUID(row["id"]))
            parent.created_at = parent.completed_at = old
            session.flush()
            retain_campaigns(
                session,
                datetime.now(UTC) - timedelta(days=395),
                lambda column: column == UUID(tenant[0]),
            )
        with sessions() as session:
            assert session.get(CampaignRecord, UUID(row["id"])) is None
            costs, missing = collect_run_costs(session, UUID(tenant[0]), all_ids, None)
            assert len(costs) == 4 and missing == 1
            assert sum(r.known_cost_subtotal_usd for r in costs) == Decimal(
                report["known_spend_subtotal_usd"]
            )
    finally:
        executor.close()
