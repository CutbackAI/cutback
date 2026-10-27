"""Real PG acceptance of content-free traces and externally asserted results."""

import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update

from cutback.server.db import Environment, StoredEvent
from cutback.server.trace_db import ExecutionSpan, ExecutionTrace, ExternalEvaluation

pytestmark = pytest.mark.postgres


def span(**values):
    start = time.time_ns() - 1_000_000_000
    return {
        "trace_id": "a" * 32,
        "span_id": "1" * 16,
        "parent_span_id": None,
        "session_id": "synthetic-session",
        "operation": "support.answer",
        "kind": "agent",
        "service": "synthetic-support",
        "workflow": "answer",
        "release": "synthetic-v1",
        "started_ns": start,
        "ended_ns": start + 500_000_000,
        "status": "unknown",
        **values,
    }


def result(**values):
    return {
        "result_id": str(uuid4()),
        "source": "synthetic-checks",
        "evaluator": "task.correct",
        "evaluator_version": "1",
        "trace_id": "a" * 32,
        "span_id": None,
        "session_id": None,
        "score_type": "boolean",
        "score": False,
        "outcome": "failed",
        "error_code": None,
        "observed_at": datetime.now(UTC).isoformat(),
        **values,
    }


def env_id(app, project, name="staging"):
    with app.state.sessions() as session:
        return str(
            session.scalar(
                select(Environment.id).where(
                    Environment.project_id == UUID(project), Environment.name == name
                )
            )
        )


def trace_rows(client, project, reader, env):
    response = client.get(
        f"/v1/projects/{project}/traces", headers=reader, params={"environment_id": env}
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def detail(client, project, reader, env, **params):
    rows = trace_rows(client, project, reader, env)
    assert len(rows) == 1
    response = client.get(
        f"/v1/projects/{project}/traces/{rows[0]['id']}", headers=reader, params=params
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_late_join_duplicate_conflict_and_unknown(api, tenant):
    client, _, app = api
    project, writer, reader = tenant
    env = env_id(app, project)
    evaluation = result(span_id="2" * 16)
    assert (
        client.post(
            "/v1/ingest/evaluations", headers=writer, json={"results": [evaluation]}
        ).json()["accepted"]
        == 1
    )
    root = span()
    child = span(
        span_id="2" * 16, parent_span_id=root["span_id"], kind="retriever", operation="retrieve"
    )
    assert (
        client.post("/v1/ingest/spans", headers=writer, json={"spans": [child]}).json()["accepted"]
        == 1
    )
    before = detail(client, project, reader, env)
    assert before["spans"][0]["issues"] == ["parent_not_in_retained_page"]
    assert "root_not_observed" in before["integrity"]
    assert before["evaluations"][0]["target_in_retained_page"] is True
    assert before["evaluations"][0]["evidence"] == "externally_asserted"
    for _ in range(2):
        response = client.post("/v1/ingest/spans", headers=writer, json={"spans": [root, child]})
        assert response.status_code == 200, response.text
    after = detail(client, project, reader, env)
    assert after["returned_spans"] == 2
    assert after["business_success"] == "unknown" and after["full_trace_cost_usd"] is None
    assert after["spans"][0]["status"] == "unknown"
    assert after["page_known_llm_cost_usd"] is None
    conflict = {**root, "status": "error"}
    assert (
        client.post(
            "/v1/ingest/spans",
            headers=writer,
            json={"spans": [span(span_id="0" * 15 + "1"), conflict]},
        ).status_code
        == 409
    )
    assert detail(client, project, reader, env)["returned_spans"] == 2
    duplicate = client.post(
        "/v1/ingest/evaluations", headers=writer, json={"results": [evaluation]}
    )
    assert duplicate.json()["duplicates"] == 1
    assert (
        client.post(
            "/v1/ingest/evaluations",
            headers=writer,
            json={"results": [{**evaluation, "score": True}]},
        ).status_code
        == 409
    )


def test_scope_environments_and_retention_export_delete(api, tenant):
    client, admin, app = api
    project, writer, reader = tenant
    other = client.post(
        "/v1/projects", headers=admin, json={"slug": "synthetic-other-" + uuid4().hex}
    ).json()["id"]
    assert (
        client.post(
            f"/v1/projects/{project}/environments", headers=admin, json={"name": "production"}
        ).status_code
        == 201
    )
    second = client.post(
        f"/v1/projects/{project}/keys",
        headers=admin,
        json={"role": "ingest", "environment": "production"},
    ).json()
    second_writer = {"Authorization": "Bearer " + second["token"]}
    record = span()
    for headers in (reader, admin, {}):
        assert client.post(
            "/v1/ingest/spans", headers=headers, json={"spans": [record]}
        ).status_code in {401, 403}
        assert client.post(
            "/v1/ingest/evaluations", headers=headers, json={"results": [result()]}
        ).status_code in {401, 403}
    for headers in (writer, second_writer):
        assert (
            client.post("/v1/ingest/spans", headers=headers, json={"spans": [record]}).status_code
            == 200
        )
    staging = env_id(app, project)
    prod = env_id(app, project, "production")
    assert (
        len(trace_rows(client, project, reader, staging))
        == len(trace_rows(client, project, reader, prod))
        == 1
    )
    row = trace_rows(client, project, reader, staging)[0]
    assert client.get(f"/v1/projects/{other}/traces/{row['id']}", headers=reader).status_code == 403
    assert client.get(f"/v1/projects/{other}/traces/{row['id']}", headers=admin).status_code == 404
    assert (
        client.get(
            f"/v1/projects/{project}/traces", headers=writer, params={"environment_id": staging}
        ).status_code
        == 403
    )
    assert trace_rows(client, project, reader, env_id(app, other)) == []
    evaluation = result()
    assert (
        client.post(
            "/v1/ingest/evaluations", headers=writer, json={"results": [evaluation]}
        ).status_code
        == 200
    )
    exported = client.get(f"/v1/projects/{project}/export", headers=admin).text
    assert '"execution_spans"' in exported and '"external_evaluations"' in exported
    from cutback.server.operations import maintain

    report = maintain(
        app.state.sessions,
        project_id=UUID(project),
        now=datetime.now(UTC) + timedelta(days=40),
        apply=True,
    )
    assert report["apply"] is True
    assert trace_rows(client, project, reader, staging) == []
    with app.state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ExternalEvaluation)
                .where(ExternalEvaluation.project_id == UUID(project))
            )
            == 0
        )
    assert (
        client.post("/v1/ingest/spans", headers=writer, json={"spans": [record]}).status_code == 200
    )
    slug = client.get(f"/v1/projects/{project}", headers=admin).json()["slug"]
    assert (
        client.delete(
            f"/v1/projects/{project}", headers=admin, params={"confirm_slug": slug}
        ).status_code
        == 200
    )
    with app.state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ExecutionTrace)
                .where(ExecutionTrace.project_id == UUID(project))
            )
            == 0
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("attributes", {"input": "SYNTHETIC_SECRET"}),
        ("operation", "SYNTHETIC SECRET"),
        ("started_ns", -1),
        ("ended_ns", 1),
        ("span_id", "0" * 16),
        ("trace_id", "0" * 32),
        ("status", "success"),
        ("started_ns", str(time.time_ns())),
    ],
)
def test_reject_content_and_invalid_spans(api, tenant, field, value):
    client, _, _ = api
    _, writer, _ = tenant
    response = client.post(
        "/v1/ingest/spans", headers=writer, json={"spans": [span(**{field: value})]}
    )
    assert response.status_code == 422
    assert "SYNTHETIC_SECRET" not in response.text


@pytest.mark.parametrize(
    "values",
    [
        {"score_type": "numeric", "score": True},
        {"score_type": "boolean", "score": 1},
        {"score_type": "numeric", "score": "NaN"},
        {"score_type": "none", "score": "bad"},
        {"outcome": "error"},
        {"outcome": "unknown"},
        {"session_id": "both"},
        {"rationale": "SYNTHETIC_SECRET"},
        {"observed_at": "2026-01-01T00:00:00"},
    ],
)
def test_reject_invalid_evaluations(api, tenant, values):
    client, _, _ = api
    _, writer, _ = tenant
    response = client.post(
        "/v1/ingest/evaluations", headers=writer, json={"results": [result(**values)]}
    )
    assert response.status_code == 422
    assert "SYNTHETIC_SECRET" not in response.text


def test_graph_cycles_clock_skew_and_truncation(api, tenant):
    client, _, app = api
    project, writer, reader = tenant
    start = time.time_ns() - 1_000_000_000
    batch = [
        span(span_id="1" * 16, parent_span_id="2" * 16, started_ns=start, ended_ns=start + 100),
        span(span_id="2" * 16, parent_span_id="1" * 16, started_ns=start - 1, ended_ns=start + 200),
    ]
    assert client.post("/v1/ingest/spans", headers=writer, json={"spans": batch}).status_code == 200
    report = detail(client, project, reader, env_id(app, project))
    assert all("parent_cycle" in s["issues"] for s in report["spans"])
    assert "outside_parent_interval" in report["spans"][0]["issues"]
    limited = detail(client, project, reader, env_id(app, project), limit=1)
    assert limited["truncated"] and "span_page_truncated" in limited["integrity"]
    assert limited["full_trace_cost_usd"] is None


def test_concurrent_duplicates_and_cursor_sessions(api, tenant):
    client, _, app = api
    project, writer, reader = tenant
    batch = {"spans": [span()]}
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(
            pool.map(
                lambda _: client.post("/v1/ingest/spans", headers=writer, json=batch), range(4)
            )
        )
    assert all(r.status_code == 200 for r in responses)
    assert sum(r.json()["accepted"] for r in responses) == 1
    second = span(trace_id="b" * 32, session_id="synthetic-session-two")
    assert (
        client.post("/v1/ingest/spans", headers=writer, json={"spans": [second]}).status_code == 200
    )
    env = env_id(app, project)
    for resource in ("traces", "sessions"):
        first = client.get(
            f"/v1/projects/{project}/{resource}",
            headers=reader,
            params={"environment_id": env, "limit": 1},
        ).json()
        next_page = client.get(
            f"/v1/projects/{project}/{resource}",
            headers=reader,
            params={"environment_id": env, "limit": 1, "before": first["next_cursor"]},
        ).json()
        assert first["items"][0]["id"] != next_page["items"][0]["id"]
        assert next_page["next_cursor"] is None
    sessions = client.get(
        f"/v1/projects/{project}/sessions", headers=reader, params={"environment_id": env}
    ).json()["items"]
    selected = next(s for s in sessions if s["session_id"] == "synthetic-session")
    session_result = result(
        trace_id=None,
        session_id="synthetic-session",
        score_type="numeric",
        score="0.50",
        outcome="passed",
    )
    assert (
        client.post(
            "/v1/ingest/evaluations", headers=writer, json={"results": [session_result]}
        ).status_code
        == 200
    )
    results = client.get(
        f"/v1/projects/{project}/sessions/{selected['id']}/evaluations", headers=reader
    ).json()
    assert results["items"][0]["score"] == "0.50"
    assert results["items"][0]["evidence"] == "externally_asserted"
    filtered = client.get(
        f"/v1/projects/{project}/traces",
        headers=reader,
        params={"environment_id": env, "session_record_id": selected["id"]},
    ).json()
    assert len(filtered["items"]) == 1


def test_canonical_native_cost_no_duplicate_spend(api, tenant, event):
    client, _, app = api
    project, writer, reader = tenant
    record = span(span_id=event.span_id, trace_id=event.trace_id, kind="llm")
    assert (
        client.post("/v1/ingest/spans", headers=writer, json={"spans": [record]}).status_code == 200
    )
    external = event.model_copy(
        update={
            "event_id": uuid4(),
            "sdk_version": "external-otel-v1",
            "sampling_policy_id": "external-otel-spans-v1",
        }
    )
    for item in (event, external):
        assert (
            client.post(
                "/v1/ingest/events",
                headers=writer,
                json={"project_id": project, "events": [item.model_dump(mode="json")]},
            ).status_code
            == 200
        )
    report = detail(client, project, reader, env_id(app, project))
    native = report["spans"][0]["native"]
    assert native["status"] == "canonical_native" and native["observations"] == 2
    assert native["event_id"] == str(event.event_id)
    assert report["page_known_llm_cost_usd"] == native["cost_usd"]
    assert report["full_trace_cost_usd"] is None and native["replay_supported"] is False
    usage = client.get(f"/v1/projects/{project}/usage", headers=reader).json()["summary"]
    assert usage["observed_attempts"] == 1
    assert usage["known_modeled_cost_subtotal_usd"] == native["cost_usd"]
    extra_native = event.model_copy(update={"event_id": uuid4()})
    assert (
        client.post(
            "/v1/ingest/events",
            headers=writer,
            json={"project_id": project, "events": [extra_native.model_dump(mode="json")]},
        ).status_code
        == 200
    )
    ambiguous = detail(client, project, reader, env_id(app, project))
    assert ambiguous["spans"][0]["native"]["status"] == "ambiguous"
    assert ambiguous["page_known_llm_cost_usd"] is None
    old = (datetime.now(UTC) - timedelta(days=40)).replace(hour=12)
    with app.state.sessions.begin() as session:
        session.execute(
            update(StoredEvent)
            .where(StoredEvent.project_id == UUID(project))
            .values(occurred_at=old)
        )
        session.execute(
            update(StoredEvent)
            .where(
                StoredEvent.project_id == UUID(project), StoredEvent.event_id == external.event_id
            )
            .values(occurred_at=old + timedelta(days=1))
        )
    from cutback.server.operations import maintain

    maintain(app.state.sessions, project_id=UUID(project), apply=True)
    retained_usage = client.get(
        f"/v1/projects/{project}/usage",
        headers=reader,
        params={"start": old.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()},
    ).json()["summary"]
    assert retained_usage["observed_attempts"] == 2


def test_projected_openinference_privacy_partial_success(api, tenant):
    from test_otel import export

    client, _, app = api
    project, writer, reader = tenant
    payload = export("openai")
    parent = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    parent["attributes"] += [
        {"key": "openinference.span.kind", "value": {"stringValue": "RETRIEVER"}},
        {"key": "session.id", "value": {"stringValue": "synthetic-session"}},
        {"key": "input.value", "value": {"stringValue": "SYNTHETIC_SECRET"}},
    ]
    parent["status"]["code"] = 0
    invalid = copy.deepcopy(parent)
    invalid["spanId"] = "0" * 16
    payload["resourceSpans"][0]["scopeSpans"][0]["spans"].append(invalid)
    response = client.post("/v1/ingest/workflow-otel/v1/traces", headers=writer, json=payload)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["accepted"] == 1 and receipt["partialSuccess"]["rejectedSpans"] == "1"
    assert receipt["projected_attributes"] > 0
    report = detail(client, project, reader, env_id(app, project))
    assert report["spans"][0]["kind"] == "retriever"
    assert report["spans"][0]["status"] == "unknown"
    assert "SYNTHETIC_SECRET" not in json.dumps(report)
    with app.state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(StoredEvent)
                .where(StoredEvent.project_id == UUID(project))
            )
            == 0
        )
        serialized = json.dumps(
            session.scalar(
                select(ExecutionSpan.metadata_record).where(
                    ExecutionSpan.project_id == UUID(project)
                )
            )
        )
        assert (
            "SYNTHETIC_SECRET" not in serialized and "THIS_NAME_IS_NOT_PERSISTED" not in serialized
        )
    with app.state.sessions.begin() as session:
        session.execute(
            update(ExecutionSpan)
            .where(ExecutionSpan.project_id == UUID(project))
            .values(started_ns=1)
        )


def test_qualified_producer_and_large_trace_bounds(api, tenant):
    import runpy
    from pathlib import Path

    client, _, app = api
    project, writer, reader = tenant
    fixture = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "examples/workflow-tracing/sample.py")
    )
    payload, evaluations = fixture["demonstration"]()
    assert (
        client.post("/v1/ingest/evaluations", headers=writer, json=evaluations).status_code == 200
    )
    response = client.post("/v1/ingest/workflow-otel/v1/traces", headers=writer, json=payload)
    assert response.status_code == 200 and response.json()["accepted"] == 4
    env = env_id(app, project)
    report = detail(client, project, reader, env)
    assert report["observed_execution_errors"] == 1
    assert {s["kind"] for s in report["spans"]} == {"agent", "retriever", "tool", "llm"}
    assert report["evaluations"][0]["outcome"] == "failed"
    assert "SYNTHETIC_CONTENT_NOT_RETAINED" not in json.dumps(report)
    session_rows = client.get(
        f"/v1/projects/{project}/sessions",
        headers=reader,
        params={"environment_id": env, "errors_only": True},
    ).json()["items"]
    assert len(session_rows) == 1 and session_rows[0]["observed_execution_errors"] == 1
    absent = client.get(
        f"/v1/projects/{project}/traces",
        headers=reader,
        params={"environment_id": env, "workflow": "absent"},
    ).json()
    assert absent["items"] == []
    for filters in (
        {"start": (datetime.now(UTC) + timedelta(days=1)).isoformat()},
        {"service": "absent"},
        {"release": "absent"},
        {"model": "unknown-model"},
    ):
        assert (
            client.get(
                f"/v1/projects/{project}/traces",
                headers=reader,
                params={"environment_id": env, **filters},
            ).json()["items"]
            == []
        )
    invalid_time = client.get(
        f"/v1/projects/{project}/traces",
        headers=reader,
        params={"environment_id": env, "start": "2026-10-27T00:00:00"},
    )
    assert invalid_time.status_code == 422
    record = span(trace_id=report["trace_id"])
    for start in range(0, 1001, 200):
        batch = [
            {**record, "span_id": f"{i + 100:016x}", "parent_span_id": "f" * 16}
            for i in range(start, min(start + 200, 1001))
        ]
        assert (
            client.post("/v1/ingest/spans", headers=writer, json={"spans": batch}).status_code
            == 200
        )
    report = detail(client, project, reader, env)
    assert report["truncated"] and report["returned_spans"] == 1000
    assert report["full_trace_cost_usd"] is None
    # Caller-provided cursor identity must not cross a tenant or environment.
    invalid = client.get(
        f"/v1/projects/{project}/traces",
        headers=reader,
        params={"environment_id": env, "before": str(uuid4())},
    )
    assert invalid.status_code == 400 and invalid.json()["code"] == "CURSOR_EXPIRED_OR_OUT_OF_SCOPE"
