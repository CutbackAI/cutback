from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from support_episode_judge import semantic_response
from support_native_agent import native_executor, response_body
from test_native_episodes import prepared_episode

from cutback.server.db import Environment, ExperimentRunRecord
from cutback.server.execution_db import ExecutionCall, ExecutionEpisode
from cutback.server.operations import maintain
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


def complete_episode(api, tenant, tmp_path, monkeypatch, *, semantic=False):
    base, store, run_id, _ = prepared_episode(api, tenant, tmp_path, monkeypatch, semantic=semantic)
    executor = native_executor(
        "responses.create",
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json=semantic_response(response_body("responses.create"))
            if b"cutback_episode_judgments" in request.content
            else response_body(
                "responses.create",
                "final" if b"function_call_output" in request.content else "tool",
            ),
        ),
    )
    try:
        result = run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=run_id,
        )
        assert result["status"] == "COMPLETED", result
    finally:
        executor.close()
    return base, run_id


@pytest.mark.parametrize("semantic", [False, True])
def test_receipt_pagination_rbac_export_retention_and_delete_preserve_unknown_semantics(
    api, tenant, tmp_path, monkeypatch, semantic
):
    base, run_id = complete_episode(api, tenant, tmp_path, monkeypatch, semantic=semantic)
    count = 6 if semantic else 4
    client, admin, app = api
    first = client.get(base + f"/experiments/{run_id}/calls?limit=2", headers=tenant[2])
    assert first.status_code == 200, first.text
    page = first.json()
    assert (
        len(page["items"]) == 2 and page["next_after"] == 2 and page["requests_reserved"] == count
    )
    second = client.get(
        base + f"/experiments/{run_id}/calls?limit=2&after=2", headers=tenant[2]
    ).json()
    assert [r["sequence"] for r in second["items"]] == [3, 4]
    assert second["next_after"] == (4 if semantic else None)
    one = client.get(base + f"/experiments/{run_id}/episodes?limit=1", headers=tenant[2]).json()
    two = client.get(
        base + f"/experiments/{run_id}/episodes?limit=1&after={one['next_after']}",
        headers=tenant[2],
    ).json()
    assert one["items"][0]["id"] != two["items"][0]["id"] and two["next_after"] is None
    assert client.get(base + f"/experiments/{run_id}/calls", headers=tenant[1]).status_code == 403
    other = client.post(
        "/v1/projects", headers=admin, json={"slug": "other-episode-" + uuid4().hex}
    ).json()["id"]
    assert (
        client.get(f"/v1/projects/{other}/experiments/{run_id}/calls", headers=admin).status_code
        == 404
    )
    assert (
        client.get(
            f"/v1/projects/{other}/experiments/{run_id}/calls", headers=tenant[2]
        ).status_code
        == 403
    )
    with app.state.sessions.begin() as session:
        calls = session.scalars(select(ExecutionCall).where(ExecutionCall.run_id == run_id)).all()
        for row in calls:
            row.completed_at = datetime.now(UTC) - timedelta(days=40)
        for row in session.scalars(
            select(ExecutionEpisode).where(ExecutionEpisode.run_id == run_id)
        ):
            row.completed_at = datetime.now(UTC) - timedelta(days=40)
        known_subtotal = session.get(ExperimentRunRecord, run_id).spent_usd
    maintain(app.state.sessions, apply=True, project_id=UUID(tenant[0]))
    after = client.get(base + f"/experiments/{run_id}/calls", headers=tenant[2]).json()
    assert (
        after["retained_receipts"] == 0
        and after["requests_reserved"] == after["known_cost_requests"] == count
    )
    assert (
        Decimal(after["known_cost_subtotal_usd"]) == known_subtotal and after["cost_coverage"] == 1
    )
    export = client.get(base + "/export", headers=admin)
    assert "Owned synthetic conversation" not in export.text
    from cutback.server.db import Project

    with app.state.sessions() as session:
        slug = session.get(Project, UUID(tenant[0])).slug
    deleted = client.delete(base, headers=admin, params={"confirm_slug": slug})
    assert deleted.status_code == 200, deleted.text
    assert client.get(base + f"/experiments/{run_id}/calls", headers=admin).status_code == 404


def test_database_rejects_same_project_wrong_environment_episode_call(
    api, tenant, tmp_path, monkeypatch
):
    _, run_id = complete_episode(api, tenant, tmp_path, monkeypatch)
    sessions = api[2].state.sessions
    with sessions.begin() as session:
        environment = Environment(project_id=UUID(tenant[0]), name="isolated-other-environment")
        session.add(environment)
        session.flush()
        environment_id = environment.id
    with pytest.raises(IntegrityError):
        with sessions.begin() as session:
            source = session.scalar(
                select(ExecutionCall).where(ExecutionCall.run_id == run_id).limit(1)
            )
            values = {c.name: getattr(source, c.name) for c in ExecutionCall.__table__.columns}
            session.add(
                ExecutionCall(
                    **{**values, "id": uuid4(), "sequence": 100, "environment_id": environment_id}
                )
            )
