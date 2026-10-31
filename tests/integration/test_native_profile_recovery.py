"""Uncertain transaction acknowledgements never repeat charged encrypted storage reads."""

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import UUID

import pytest
from integration.test_native_request_profiles import queue_analysis, retained_profile_stream

from cutback.server.native_profile_db import NativeProfileAnalysis
from cutback.server.native_profile_service import run_native_profile_one

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("lost_state", ["RUNNING", "COMPLETED"])
def test_lost_analysis_commit_ack_is_reconciled_without_repeating_storage_reads(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    event,
    lost_state,
):
    sample, store, _ = retained_profile_stream(api, tenant, tmp_path, monkeypatch, event)
    queued, body = queue_analysis(api, tenant, sample)
    assert queued.status_code == 201
    identifier = UUID(body["request_id"])
    sessions = api[2].state.sessions
    original, reads, lost = sessions.begin, [], []
    get = store.get

    def counted(*args):
        reads.append(1)
        return get(*args)

    @contextmanager
    def uncertain():
        with original() as session:
            yield session
        with sessions() as session:
            row = session.get(NativeProfileAnalysis, identifier)
            matches = row is not None and row.state == lost_state
        if matches and not lost:
            lost.append(1)
            raise ConnectionError("PRIVATE_OWNED_ANALYSIS_ACK_LOST")

    monkeypatch.setattr(store, "get", counted)
    with monkeypatch.context() as fault:
        fault.setattr(sessions, "begin", uncertain)
        result = run_native_profile_one(sessions, store, only_analysis_id=identifier)
    assert lost
    assert result["status"] == ("FAILED" if lost_state == "RUNNING" else "COMPLETED")
    assert len(reads) == (0 if lost_state == "RUNNING" else 2)
    before = len(reads)
    assert run_native_profile_one(sessions, store, only_analysis_id=identifier)["status"] == "IDLE"
    assert len(reads) == before
    with sessions() as session:
        row = session.get(NativeProfileAnalysis, identifier)
        assert row.attempts == 1
        assert row.state == result["status"]


def test_two_workers_share_one_claim_and_do_not_repeat_encrypted_reads(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    event,
):
    sample, store, _ = retained_profile_stream(api, tenant, tmp_path, monkeypatch, event)
    queued, body = queue_analysis(api, tenant, sample)
    assert queued.status_code == 201
    entered, release = threading.Event(), threading.Event()
    original, reads = store.get, []

    def held(*args):
        reads.append(1)
        if len(reads) == 1:
            entered.set()
            assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(store, "get", held)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            run_native_profile_one,
            api[2].state.sessions,
            store,
            only_analysis_id=body["request_id"],
        )
        try:
            assert entered.wait(5)
            second = pool.submit(
                run_native_profile_one,
                api[2].state.sessions,
                store,
                only_analysis_id=body["request_id"],
            )
            assert second.result(5)["status"] == "IDLE"
        finally:
            release.set()
        assert first.result(5)["status"] == "COMPLETED"
    assert len(reads) == 2


def test_cooperative_deadline_after_one_read_is_terminal_and_truncation_abstains(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    event,
):
    sample, store, _ = retained_profile_stream(api, tenant, tmp_path, monkeypatch, event)
    queued, body = queue_analysis(api, tenant, sample)
    assert queued.status_code == 201
    original, reads, ticks = store.get, [], iter([0, 0, 1000])

    def counted(*args):
        reads.append(1)
        return original(*args)

    monkeypatch.setattr(store, "get", counted)
    assert (
        run_native_profile_one(
            api[2].state.sessions,
            store,
            only_analysis_id=body["request_id"],
            clock=lambda: next(ticks),
        )["status"]
        == "FAILED"
    )
    assert len(reads) == 1
    assert (
        run_native_profile_one(api[2].state.sessions, store, only_analysis_id=body["request_id"])[
            "status"
        ]
        == "IDLE"
    )
    queued, body = queue_analysis(api, tenant, sample, max_observations=1)
    assert queued.status_code == 201
    assert (
        run_native_profile_one(api[2].state.sessions, store, only_analysis_id=body["request_id"])[
            "status"
        ]
        == "COMPLETED"
    )
    with api[2].state.sessions() as session:
        row = session.get(NativeProfileAnalysis, UUID(body["request_id"]))
        assert row.summary["profiled_attempts"] == 1
        assert row.summary["cache_trial_eligible"] is False
        assert "RETAINED_WINDOW_TRUNCATED" in row.summary["abstention_reasons"]
