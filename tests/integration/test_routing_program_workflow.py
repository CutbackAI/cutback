"""Native SDK transports, real PostgreSQL and the complete conditional routing path."""

import json
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from support_agent import native_response
from test_routing_program_api import prepared_program

from cutback.evals.executors import OpenAIExecutor
from cutback.evals.experiments import route_target
from cutback.routing_changes import RoutingSourceProposal, prepare_patch
from cutback.server.db import ExperimentRunRecord
from cutback.server.routing_program_db import (
    RoutingProgramRecord,
    RoutingProgramRun,
    RoutingProgramSelection,
)
from cutback.server.routing_program_worker import advance_one
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


def native(
    monkeypatch, *, simple_passes=False, unknown=False, before_reply=None, reject_candidate=None
):
    calls = []
    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "owned-synthetic-only")

    def handle(request):
        assert str(request.url) == "https://api.openai.com/v1/responses"
        body = json.loads(request.content)
        calls.append({"model": body["model"], "input_bytes": len(body["input"].encode())})
        response = native_response(request).json()
        output = "Urgent" if "Priority: urgent." in body["input"] else "Normal"
        if body["model"] == "gpt-4.1-nano" or (
            not simple_passes
            and body["model"] == "gpt-4.1-mini"
            and len(body["input"].encode()) > 100
        ):
            output = "Normal"
        if reject_candidate and reject_candidate() and body["model"] != "gpt-4.1":
            output = "Normal" if output == "Urgent" else "Urgent"
        response["output"][0]["content"][0]["text"] = output
        if unknown:
            response.pop("usage", None)
        if before_reply:
            before_reply()
        return httpx.Response(200, json=response)

    executor = OpenAIExecutor(
        http_client=httpx.Client(
            transport=httpx.MockTransport(handle), trust_env=False, follow_redirects=False
        )
    )

    class RoutingExecutor:
        synthetic_transport = True

        def execute(self, model, text, timeout_seconds):
            if model.provider == "router":
                model = route_target(model, text)
            return executor.execute(model, text, timeout_seconds)

        def close(self):
            executor.close()

    return RoutingExecutor(), calls


def advance(api, store, identifier):
    with api[2].state.sessions.begin() as session:
        session.get(RoutingProgramRecord, UUID(identifier)).available_at = None
    return advance_one(
        api[2].state.sessions,
        store,
        execution_enabled=True,
        only_program_id=identifier,
        synthetic_transport=True,
    )


def pending_child(api, identifier):
    with api[2].state.sessions() as session:
        return session.execute(
            select(RoutingProgramRun.phase, ExperimentRunRecord.id)
            .join(ExperimentRunRecord, RoutingProgramRun.run_id == ExperimentRunRecord.id)
            .where(
                RoutingProgramRun.program_id == UUID(identifier),
                ExperimentRunRecord.status == "QUEUED",
            )
        ).one()


@pytest.mark.parametrize("simple_passes", [True, False])
@pytest.mark.parametrize("confirmation_fails", [False, True])
def test_one_approval_skips_or_runs_training_then_confirms_once_without_replay(
    api, tenant, tmp_path, monkeypatch, simple_passes, confirmation_fails
):
    base, root, store, row, _ = prepared_program(api, tenant, tmp_path, monkeypatch)
    response = api[0].post(
        base + "/routing/programs/" + row["id"] + "/approve",
        headers=api[1],
        json={
            "program_hash": row["program_hash"],
            "approval_ref": "owned-synthetic-conditional-routing",
        },
    )
    assert response.status_code == 200, response.text
    reject = [False]
    executor, calls = native(
        monkeypatch, simple_passes=simple_passes, reject_candidate=lambda: reject[0]
    )
    phases = []
    try:
        result = advance(api, store, row["id"])
        for _ in range(4):
            with api[2].state.sessions() as session:
                parent = session.get(RoutingProgramRecord, UUID(row["id"]))
                assert parent.status != "FAILED", parent.summary
            if result["status"] == "COMPLETED":
                break
            phase, run_id = pending_child(api, row["id"])
            phases.append(phase)
            reject[0] = confirmation_fails and phase == "confirmation"
            count = len(calls)
            observed = advance(api, store, row["id"])
            assert observed["status"] == result["status"] and len(calls) == count
            result_run = run_one(
                api[2].state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=run_id,
            )
            assert result_run["status"] == "COMPLETED", result_run.get("results", {}).get("code")
            completed_count = len(calls)
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
            assert len(calls) == completed_count
            result = advance(api, store, row["id"])
        assert result["status"] == "COMPLETED", result
        assert phases == (
            ["static_validation", "confirmation"]
            if simple_passes
            else ["static_validation", "training", "routed_validation", "confirmation"]
        )
        with api[2].state.sessions() as session:
            parent = session.get(RoutingProgramRecord, UUID(row["id"]))
            selection = session.scalar(
                select(RoutingProgramSelection).where(
                    RoutingProgramSelection.program_id == parent.id
                )
            )
            children = session.scalars(
                select(ExperimentRunRecord)
                .join(RoutingProgramRun, RoutingProgramRun.run_id == ExperimentRunRecord.id)
                .where(RoutingProgramRun.program_id == parent.id)
            ).all()
            assert (
                parent.requests_used == len(calls) == sum(child.requests_used for child in children)
            )
            assert parent.spent_usd == sum(child.spent_usd for child in children)
            assert parent.known_cost_requests == len(calls)
            assert parent.approval_status == "consumed"
            assert selection.strategy == (
                "static_native" if simple_passes else "learned_input_size"
            )
            assert (parent.fit_id is None) is simple_passes
            assert parent.summary["sample_only_gate"]["passed"] is not confirmation_fails, (
                parent.summary
            )
            assert parent.summary["fit_compute_usd"] is None
            assert parent.summary["production_savings_usd"] is None
        response = api[0].post(
            base + "/routing/programs/" + row["id"] + "/source-proposal",
            headers=api[1],
            json={"purpose": "owned-source-review"},
        )
        if confirmation_fails:
            assert response.status_code == 409, response.text
            assert "ROUTING_PROGRAM_CURRENT_CONFIRMED_SOURCE_REQUIRED" in response.text
            count = len(calls)
            assert advance(api, store, row["id"])["status"] == "IDLE"
            assert len(calls) == count
            with api[2].state.sessions() as session:
                assert session.get(RoutingProgramRecord, UUID(row["id"])).fit_id is None or (
                    not simple_passes
                )
                assert (
                    len(
                        session.scalars(
                            select(RoutingProgramSelection).where(
                                RoutingProgramSelection.program_id == UUID(row["id"])
                            )
                        ).all()
                    )
                    == 1
                )
            return
        assert response.status_code == 200, response.text
        source = RoutingSourceProposal.model_validate(response.json())
        artifact = prepare_patch(root, source)
        assert artifact["applied"] is False and "routing.json" in artifact["diff"]
        assert (
            source.full_task_cost_known is False
            and source.decision == "synthetic_local_review_only"
        )
        count = len(calls)
        assert advance(api, store, row["id"])["status"] == "IDLE"
        assert len(calls) == count
    finally:
        executor.close()


def test_static_child_cannot_construct_a_live_provider_from_a_fixture_parent(
    api, tenant, tmp_path, monkeypatch
):
    base, _, store, row, _ = prepared_program(api, tenant, tmp_path, monkeypatch)
    assert (
        api[0]
        .post(
            base + "/routing/programs/" + row["id"] + "/approve",
            headers=api[1],
            json={
                "program_hash": row["program_hash"],
                "approval_ref": "owned-fixture-only",
            },
        )
        .status_code
        == 200
    )
    advance(api, store, row["id"])
    _, run_id = pending_child(api, row["id"])
    constructed = []

    def refuse_constructor():
        constructed.append(True)
        raise AssertionError("Live provider construction is outside this fixture approval")

    monkeypatch.setattr("cutback.evals.executors.OpenAIExecutor", refuse_constructor)
    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "owned-ambient-key-must-not-be-read")
    result = run_one(api[2].state.sessions, store, execution_enabled=True, only_run_id=run_id)
    assert result["status"] == "FAILED" and result["provider_calls"] == 0
    assert not constructed
    with api[2].state.sessions() as session:
        assert session.get(RoutingProgramRecord, UUID(row["id"])).requests_used == 0


def test_content_revocation_after_first_request_stops_next_physical_call(
    api, tenant, tmp_path, monkeypatch
):
    from cutback.agent import load_session

    base, root, store, row, _ = prepared_program(api, tenant, tmp_path, monkeypatch)
    assert (
        api[0]
        .post(
            base + "/routing/programs/" + row["id"] + "/approve",
            headers=api[1],
            json={
                "program_hash": row["program_hash"],
                "approval_ref": "owned-revocation-test",
            },
        )
        .status_code
        == 200
    )
    policy_id = load_session(root)["policy_id"]
    replies = []

    def revoke():
        replies.append(1)
        assert len(replies) == 1
        response = api[0].delete(base + "/capture-policies/" + policy_id, headers=api[1])
        assert response.status_code == 200

    executor, calls = native(monkeypatch, before_reply=revoke)
    try:
        advance(api, store, row["id"])
        _, run_id = pending_child(api, row["id"])
        result = run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=run_id,
        )
        assert result["status"] == "FAILED"
        assert len(calls) == 1
        with api[2].state.sessions() as session:
            assert session.get(RoutingProgramRecord, UUID(row["id"])).requests_used == 1
        assert advance(api, store, row["id"])["status"] == "FAILED"
        assert len(calls) == 1
    finally:
        executor.close()


def test_unknown_bill_stops_program_and_keeps_attempt_spend_without_training_replay(
    api, tenant, tmp_path, monkeypatch
):
    base, _, store, row, _ = prepared_program(api, tenant, tmp_path, monkeypatch)
    assert (
        api[0]
        .post(
            base + "/routing/programs/" + row["id"] + "/approve",
            headers=api[1],
            json={
                "program_hash": row["program_hash"],
                "approval_ref": "owned-only",
            },
        )
        .status_code
        == 200
    )
    executor, calls = native(monkeypatch, unknown=True)
    try:
        advance(api, store, row["id"])
        _, run_id = pending_child(api, row["id"])
        run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=run_id,
        )
        result = advance(api, store, row["id"])
        assert result["status"] == "FAILED"
        with api[2].state.sessions() as session:
            parent = session.get(RoutingProgramRecord, UUID(row["id"]))
            assert parent.requests_used == len(calls) > 0
            assert parent.known_cost_requests == 0 and parent.reserved_usd > 0
        count = len(calls)
        assert advance(api, store, row["id"])["status"] == "IDLE"
        assert len(calls) == count
    finally:
        executor.close()
