"""Canonical PostgreSQL + real SQLite + native SDK transports, no OS-isolation claim."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select
from support_evaluation import authorized_dataset
from support_isolated_tools import record_case, record_model_reply, scoped_broker
from support_native_agent import native_executor
from unit.test_isolated_episode_contract import isolated_plan
from unit.test_tool_broker import LocalTestRuntime

from cutback.server.db import ExperimentRunRecord
from cutback.server.execution_db import ExecutionCall, ExecutionEpisode
from cutback.server.tool_db import ToolOperationRecord, ToolSessionRecord
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


def approved_record_run(
    api, tenant, tmp_path, monkeypatch, *, surface="responses.create", image=None, broker=None
):
    base, store, old_dataset, _ = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    project, principal, broker = UUID(tenant[0]), uuid4(), broker or uuid4()
    image = image or "sha256:" + "a" * 64
    response = api[0].post(
        base + "/datasets",
        headers=api[1],
        json={
            "workflow": "classify",
            "policy_id": old_dataset["provenance"]["policy_id"],
            "source_type": "synthetic_fixture",
            "independent_labels": False,
            "provenance_ref": "owned-isolated-sql-controls",
            "cases": [record_case(project, principal) for _ in range(20)],
        },
    )
    assert response.status_code == 201, response.text
    dataset = response.json()
    raw = isolated_plan(project, principal, broker, image, sample_limit=1).model_dump(mode="json")
    raw["dataset_hash"] = dataset["immutable_hash"]
    provider = "anthropic" if surface == "messages.create" else "openai"
    if provider == "anthropic":
        # The authorization helper defaults to OpenAI/local. This is a current
        # explicit synthetic content grant for the second qualified provider.
        from cutback.server.db import CapturePolicyRecord

        with api[2].state.sessions.begin() as session:
            policy = session.get(CapturePolicyRecord, UUID(old_dataset["provenance"]["policy_id"]))
            policy.policy = {
                **policy.policy,
                "allowed_providers": ["openai", "anthropic", "local"],
            }
    for target in [raw["baseline"], *raw["candidates"]]:
        target.update(
            provider=provider,
            model="claude-haiku-4-5" if provider == "anthropic" else "gpt-4.1-mini",
        )
    raw["approved_providers"] = [provider]
    if provider == "openai":
        raw["episode_execution"]["openai_surface"] = surface
    response = api[0].post(
        base + "/experiments/plans",
        headers=api[1],
        json={"dataset_id": dataset["id"], "specification": raw},
    )
    assert response.status_code == 201, response.text
    plan = response.json()
    response = api[0].post(
        base + "/experiments/plans/" + plan["id"] + "/approve",
        headers=api[1],
        json={"plan_hash": plan["plan_hash"], "approval_ref": "owned-native-tool-test"},
    )
    assert response.status_code == 200, response.text
    response = api[0].post(base + "/experiments", headers=api[1], json={"plan_id": plan["id"]})
    assert response.status_code == 202, response.text
    return base, store, UUID(response.json()["id"]), project, principal, broker, image


@pytest.mark.parametrize(
    "surface", ["responses.create", "chat.completions.create", "messages.create"]
)
def test_native_record_episode_has_canonical_tool_intents_before_io_and_metadata_only_receipts(
    api, tenant, tmp_path, monkeypatch, surface
):
    base, store, run_id, project, principal, broker, image = approved_record_run(
        api, tenant, tmp_path, monkeypatch, surface=surface
    )
    sessions, calls = api[2].state.sessions, []
    transport = __import__("httpx2" if surface == "messages.create" else "httpx")

    def model(request):
        calls.append(request)
        return transport.Response(200, json=record_model_reply(request, surface=surface))

    original = LocalTestRuntime.execute

    def checked(runtime, operation, tool, arguments):
        with sessions() as session:
            row = session.get(ToolOperationRecord, operation)
            assert (
                row
                and row.run_id == run_id
                and row.state == "DISPATCHING"
                and row.state_changed is None
            )
        return original(runtime, operation, tool, arguments)

    monkeypatch.setattr(LocalTestRuntime, "execute", checked)
    executor = native_executor(surface, monkeypatch, model)
    try:
        with scoped_broker(
            project,
            principal,
            broker,
            image,
            endpoint="npipe:////./pipe/synthetic-test-only",
            runtime_factory=LocalTestRuntime,
        ) as (client, _):
            result = run_one(
                sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=run_id,
                tool_client=client,
            )
        assert result["status"] == "COMPLETED", result
        assert len(calls) == 6
        with sessions() as session:
            rows = session.scalars(
                select(ToolSessionRecord).where(ToolSessionRecord.run_id == run_id)
            ).all()
            operations = session.scalars(
                select(ToolOperationRecord).where(ToolOperationRecord.run_id == run_id)
            ).all()
            native = session.scalars(
                select(ExecutionCall).where(ExecutionCall.run_id == run_id)
            ).all()
            episodes = session.scalars(
                select(ExecutionEpisode).where(ExecutionEpisode.run_id == run_id)
            ).all()
            assert len(rows) == len(episodes) == 2 and len(operations) == 4 and len(native) == 6
            assert all(row.cleanup_confirmed and row.state == "CLOSED" for row in rows)
            assert all(row.state == "RETURNED" for row in operations)
            assert all(
                row.summary["containment_confirmed"] and row.summary["status"] == "passed"
                for row in episodes
            )
            assert all(
                row.summary["tool_cost_usd"] is row.summary["complete_task_cost_usd"] is None
                for row in episodes
            )
        route = base + "/experiments/" + str(run_id) + "/tool-sessions"
        page = api[0].get(route, headers=tenant[2]).json()
        assert page["retained_sessions"] == 2 and page["tool_cost_usd"] is None
        assert "SYNTHETIC_HIDDEN_FIELD" not in json.dumps(page)
        first = page["items"][0]
        operations = api[0].get(route + "/" + first["id"] + "/operations", headers=tenant[2]).json()
        assert operations["retained_operations"] == 2 and "arguments" not in json.dumps(operations)
        assert (
            api[0]
            .post(
                route + "/" + first["id"] + "/containment",
                headers=tenant[2],
                json={"approval_hash": first["approval_hash"]},
            )
            .status_code
            == 403
        )
        with sessions() as session:
            assert session.get(ExperimentRunRecord, run_id).known_cost_requests == 6
    finally:
        executor.close()


def test_lost_write_ack_stops_all_further_native_requests_and_never_requeues(
    api, tenant, tmp_path, monkeypatch
):
    base, store, run_id, project, principal, broker, image = approved_record_run(
        api, tenant, tmp_path, monkeypatch
    )
    sessions, calls = api[2].state.sessions, []
    original = LocalTestRuntime.execute

    def lost_after_write(runtime, operation, tool, arguments):
        response = original(runtime, operation, tool, arguments)
        if tool == "records_patch":
            assert response["state_changed"]
            raise ConnectionError("SYNTHETIC_PRIVATE_LOST_ACK")
        return response

    monkeypatch.setattr(LocalTestRuntime, "execute", lost_after_write)

    def model(request):
        calls.append(request)
        return httpx.Response(200, json=record_model_reply(request))

    executor = native_executor("responses.create", monkeypatch, model)
    try:
        with scoped_broker(
            project,
            principal,
            broker,
            image,
            endpoint="npipe:////./pipe/synthetic-test-only",
            runtime_factory=LocalTestRuntime,
        ) as (client, _):
            result = run_one(
                sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=run_id,
                tool_client=client,
            )
            assert result["status"] == "FAILED", result
            assert len(calls) == 2
            with sessions() as session:
                row = session.get(ExperimentRunRecord, run_id)
                operations = session.scalars(
                    select(ToolOperationRecord)
                    .where(ToolOperationRecord.run_id == run_id)
                    .order_by(ToolOperationRecord.sequence)
                ).all()
                tools = session.scalars(
                    select(ToolSessionRecord).where(ToolSessionRecord.run_id == run_id)
                ).all()
                assert row.requests_used == row.known_cost_requests == 2
                assert [item.state for item in operations] == ["RETURNED", "UNCERTAIN"]
                assert operations[-1].state_changed is None
                assert len(tools) == 1 and tools[0].cleanup_confirmed
            again = run_one(
                sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=run_id,
                tool_client=client,
            )
            assert again["status"] == "IDLE" and len(calls) == 2
        exported = api[0].get(base + "/export", headers=api[1])
        assert exported.status_code == 200 and "SYNTHETIC_HIDDEN_FIELD" not in exported.text
        assert "SYNTHETIC_PRIVATE_LOST_ACK" not in exported.text
    finally:
        executor.close()


def test_cancellation_during_actual_tool_io_keeps_known_native_cost_and_queues_containment(
    api, tenant, tmp_path, monkeypatch
):
    base, store, run_id, project, principal, broker, image = approved_record_run(
        api, tenant, tmp_path, monkeypatch
    )
    sessions, calls = api[2].state.sessions, []
    original = LocalTestRuntime.execute

    def cancelled(runtime, operation, tool, arguments):
        response = original(runtime, operation, tool, arguments)
        assert (
            api[0]
            .post(base + "/experiments/" + str(run_id) + "/cancel", headers=api[1], json={})
            .status_code
            == 200
        )
        with sessions() as session:
            row = session.scalar(
                select(ToolSessionRecord).where(ToolSessionRecord.run_id == run_id)
            )
            assert row.cleanup_requested
        return response

    monkeypatch.setattr(LocalTestRuntime, "execute", cancelled)

    def model(request):
        calls.append(request)
        return httpx.Response(200, json=record_model_reply(request))

    executor = native_executor("responses.create", monkeypatch, model)
    try:
        with scoped_broker(
            project,
            principal,
            broker,
            image,
            endpoint="npipe:////./pipe/synthetic-test-only",
            runtime_factory=LocalTestRuntime,
        ) as (client, _):
            result = run_one(
                sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=run_id,
                tool_client=client,
            )
        assert result["status"] == "CANCELLED" and len(calls) == 1, result
        with sessions() as session:
            run = session.get(ExperimentRunRecord, run_id)
            assert run.requests_used == run.known_cost_requests == 1
            tool = session.scalar(
                select(ToolSessionRecord).where(ToolSessionRecord.run_id == run_id)
            )
            assert tool.cleanup_confirmed and not tool.cleanup_requested
    finally:
        executor.close()


def test_cleanup_recovery_is_finite_nonbillable_and_project_delete_waits_for_confirmation(
    api, tenant, tmp_path, monkeypatch
):
    from cutback.server.db import Project
    from cutback.server.tool_recovery import recover_tool_one

    base, store, run_id, project, principal, broker, image = approved_record_run(
        api, tenant, tmp_path, monkeypatch
    )
    sessions = api[2].state.sessions
    executor = native_executor(
        "responses.create",
        monkeypatch,
        lambda request: httpx.Response(200, json=record_model_reply(request)),
    )
    try:
        with scoped_broker(
            project,
            principal,
            broker,
            image,
            endpoint="npipe:////./pipe/synthetic-test-only",
            runtime_factory=LocalTestRuntime,
        ) as (client, _):
            assert (
                run_one(
                    sessions,
                    store,
                    executor=executor,
                    execution_enabled=True,
                    only_run_id=run_id,
                    tool_client=client,
                )["status"]
                == "COMPLETED"
            )
        with sessions.begin() as session:
            row = session.scalar(
                select(ToolSessionRecord).where(ToolSessionRecord.run_id == run_id)
            )
            row.cleanup_confirmed, row.state, row.cleanup_requested = False, "UNCERTAIN", True
            identifier, approval = row.id, row.approval_hash
            slug = session.get(Project, project).slug
        assert api[0].delete(base, headers=api[1], params={"confirm_slug": slug}).status_code == 409

        class UnavailableCleanup:
            project_id, principal_id, broker_id = project, principal, broker
            attempts = 0

            def contain(self, scope):
                self.attempts += 1
                raise ConnectionError("synthetic-only")

        cleanup = UnavailableCleanup()
        now = datetime.now(UTC)
        for attempt in range(3):
            receipt = recover_tool_one(
                sessions, client=cleanup, now=now + timedelta(seconds=181 * attempt)
            )
            assert receipt["status"] == "UNCERTAIN" and receipt["tool_operations_replayed"] == 0
        assert (
            recover_tool_one(sessions, client=cleanup, now=now + timedelta(hours=1))["status"]
            == "IDLE"
        )
        assert cleanup.attempts == 3
        route = (
            base
            + "/experiments/"
            + str(run_id)
            + "/tool-sessions/"
            + str(identifier)
            + "/containment"
        )
        assert (
            api[0].post(route, headers=api[1], json={"approval_hash": "0" * 64}).status_code == 409
        )
        with sessions.begin() as session:
            session.get(ToolSessionRecord, identifier).cleanup_lease_until = None
        assert (
            api[0].post(route, headers=api[1], json={"approval_hash": approval}).status_code == 200
        )

        class ConfirmedCleanup(UnavailableCleanup):
            def contain(self, scope):
                return {
                    **scope.model_dump(mode="json"),
                    "state": "CLOSED",
                    "cleanup_confirmed": True,
                }

        assert (
            recover_tool_one(sessions, client=ConfirmedCleanup(), now=now + timedelta(hours=1))[
                "status"
            ]
            == "CLOSED"
        )
        assert api[0].delete(base, headers=api[1], params={"confirm_slug": slug}).status_code == 200
        with sessions() as session:
            assert not session.scalar(
                select(ToolSessionRecord.id).where(ToolSessionRecord.project_id == project)
            )
            assert not session.scalar(
                select(ToolOperationRecord.id).where(ToolOperationRecord.project_id == project)
            )
    finally:
        executor.close()
