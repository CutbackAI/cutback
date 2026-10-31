"""A cache candidate uses real native calls, canonical reservations and encrypted PG state."""

from contextlib import contextmanager
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import func, select
from support_exact_cache import prepare_cache_experiment

from cutback.server.db import (
    CapturePolicyRecord,
    ExperimentPlanRecord,
    ExperimentRunRecord,
)
from cutback.server.enterprise_db import CacheEntry
from cutback.server.execution_db import ExecutionCall
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


def test_cache_candidate_reserves_worst_case_then_counts_only_fresh_usage(
    api, tenant, tmp_path, monkeypatch
):
    store, run_id, executor, calls = prepare_cache_experiment(api, tenant, tmp_path, monkeypatch)
    try:
        result = run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=run_id,
        )
        assert result["status"] == "COMPLETED", result
        report = result["results"]
        candidate = report["models"][1]
        evidence = candidate["exact_cache"]
        assert evidence["logical_requests"] == 6
        assert evidence["native_sdk_calls"] == evidence["cache_hits"] == 3
        assert evidence["cache_operations"] == 9 and evidence["execution_qualified"]
        assert evidence["cache_writes"]["created"] == 3
        assert evidence["invoice_verified_cost_usd"] is None
        assert evidence["whole_task_cost_usd"] is evidence["production_hit_rate"] is None
        assert evidence["cache_operation_cost_upper_usd"] == "9E-9"
        assert report["billing_complete"] and report["requests"] == 12 and len(calls) == 9
        recommendation = report["recommendations"][0]
        assert recommendation["cheaper_on_sample"] is True
        assert recommendation["exact_cache"]["historical_cached_usage_priced_again"] is False
        with api[2].state.sessions() as session:
            run = session.get(ExperimentRunRecord, run_id)
            receipts = session.scalars(
                select(ExecutionCall).where(ExecutionCall.run_id == run_id)
            ).all()
            assert len(receipts) == run.requests_used == run.known_cost_requests == 12
            assert all(item.state == "completed" for item in receipts)
            assert sum(item.input_tokens == item.output_tokens == 0 for item in receipts) == 3
            assert sum((item.spent_usd for item in receipts), Decimal(0)) == run.spent_usd
            assert run.spent_usd < run.reserved_usd
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(CacheEntry)
                    .where(CacheEntry.project_id == UUID(tenant[0]))
                )
                == 3
            )
        assert not executor.exact_cache_ready  # A borrowed executor keeps no stale run authority.
        repeated = run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=run_id,
        )
        assert repeated["status"] == "IDLE" and len(calls) == 9
    finally:
        executor.close()


def test_disabled_cache_stops_before_even_baseline_provider_dispatch(
    api, tenant, tmp_path, monkeypatch
):
    store, run_id, executor, calls = prepare_cache_experiment(api, tenant, tmp_path, monkeypatch)
    monkeypatch.setenv("CUTBACK_CACHE_ENABLED", "false")
    try:
        result = run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=run_id,
        )
        assert result["status"] == "FAILED" and not calls
        with api[2].state.sessions() as session:
            assert session.get(ExperimentRunRecord, run_id).requests_used == 0
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(ExecutionCall)
                    .where(ExecutionCall.run_id == run_id)
                )
                == 0
            )
        assert not executor.exact_cache_ready
    finally:
        executor.close()


@pytest.mark.parametrize("reason", ["quota", "structured_input"])
def test_cache_preflight_refuses_unavailable_capacity_or_unsupported_input_before_reservations(
    api, tenant, tmp_path, monkeypatch, reason
):
    contract = (
        {
            "sdk_surface": "responses.create",
            "parameters": ["text"],
            "payload": {
                "op": "list",
                "items": [
                    {
                        "op": "dict",
                        "items": {
                            "role": {"op": "literal", "value": "user"},
                            "content": {"op": "parameter", "name": "text"},
                        },
                    }
                ],
            },
        }
        if reason == "structured_input"
        else None
    )
    store, run_id, executor, calls = prepare_cache_experiment(
        api, tenant, tmp_path, monkeypatch, request_contract=contract
    )
    if reason == "quota":
        with api[2].state.sessions.begin() as session:
            run = session.get(ExperimentRunRecord, run_id)
            plan = session.get(ExperimentPlanRecord, run.plan_id)
            policy = session.get(CapturePolicyRecord, UUID(plan.specification["policy_id"]))
            policy.policy = {**policy.policy, "max_samples": 1}
    try:
        result = run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=run_id,
        )
        assert result["status"] == "FAILED" and not calls
        with api[2].state.sessions() as session:
            assert session.get(ExperimentRunRecord, run_id).requests_used == 0
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(ExecutionCall)
                    .where(ExecutionCall.run_id == run_id)
                )
                == 0
            )
        assert not executor.exact_cache_ready
    finally:
        executor.close()


def test_no_observed_repetition_cannot_become_a_qualified_cache_candidate(
    api, tenant, tmp_path, monkeypatch
):
    store, run_id, executor, calls = prepare_cache_experiment(
        api, tenant, tmp_path, monkeypatch, repeats=1
    )
    try:
        result = run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=run_id,
        )
        assert result["status"] == "COMPLETED" and len(calls) == 6
        candidate = result["results"]["models"][1]["exact_cache"]
        assert candidate["cache_hits"] == 0 and candidate["execution_qualified"] is False
        assert result["results"]["recommendations"][0]["quality_gate_passed"] is False
    finally:
        executor.close()


def test_unknown_native_usage_keeps_the_reservation_and_never_replays(
    api, tenant, tmp_path, monkeypatch
):
    store, run_id, executor, calls = prepare_cache_experiment(
        api, tenant, tmp_path, monkeypatch, usage=False
    )
    try:
        result = run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=run_id,
        )
        assert result["status"] == "FAILED" and len(calls) == 1
        assert result["results"]["billing_complete"] is False
        assert not result["results"]["recommendations"]
        with api[2].state.sessions() as session:
            run = session.get(ExperimentRunRecord, run_id)
            assert run.requests_used == 1 and run.known_cost_requests == 0 and run.reserved_usd > 0
            call = session.scalar(select(ExecutionCall).where(ExecutionCall.run_id == run_id))
            assert call.spent_usd is None
        repeated = run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=run_id,
        )
        assert repeated["status"] == "IDLE" and len(calls) == 1
    finally:
        executor.close()


def test_uncertain_cache_write_preserves_the_native_result_but_blocks_candidate_promotion(
    api, tenant, tmp_path, monkeypatch
):
    store, run_id, executor, calls = prepare_cache_experiment(api, tenant, tmp_path, monkeypatch)
    sessions = api[2].state.sessions
    original_begin = sessions.begin
    lost = []

    @contextmanager
    def lose_one_cache_commit_ack():
        with original_begin() as session:
            before = session.scalar(
                select(func.count())
                .select_from(CacheEntry)
                .where(CacheEntry.project_id == UUID(tenant[0]))
            )
            yield session
            session.flush()
            after = session.scalar(
                select(func.count())
                .select_from(CacheEntry)
                .where(CacheEntry.project_id == UUID(tenant[0]))
            )
        if after > before and not lost:
            lost.append(True)
            raise ConnectionError("OWNED_SYNTHETIC_CACHE_COMMIT_ACK_LOST")

    try:
        with monkeypatch.context() as fault:
            fault.setattr(sessions, "begin", lose_one_cache_commit_ack)
            result = run_one(
                sessions, store, executor=executor, execution_enabled=True, only_run_id=run_id
            )
        assert lost and result["status"] == "COMPLETED" and len(calls) == 9
        evidence = result["results"]["models"][1]["exact_cache"]
        assert evidence["cache_writes"]["uncertain"] == 1 and evidence["cache_hits"] == 3
        assert evidence["execution_qualified"] is False
        assert result["results"]["recommendations"][0]["quality_gate_passed"] is False
        assert result["results"]["billing_complete"] is True
    finally:
        executor.close()
