"""Encrypted workflow content is an explicit, scoped, audited opt-in."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event as sql_event
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from test_workflow_traces import detail, env_id, span, trace_rows

from cutback.evals.artifacts import EncryptedFileStore
from cutback.server.db import AuditEvent, CapturePolicyRecord, PayloadSample
from cutback.server.trace_db import TraceContent

pytestmark = pytest.mark.postgres


@pytest.fixture
def content(api, tenant, tmp_path, monkeypatch):
    import os

    client, admin, app = api
    project, writer, reader = tenant
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    app.state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    policy = client.post(
        f"/v1/projects/{project}/capture-policies",
        headers=admin,
        json={
            "enabled": True,
            "approval_ref": "synthetic-trace-acceptance",
            "rights_basis": "synthetic",
            "environments": ["staging"],
            "workflows": ["answer"],
            "allowed_providers": ["local"],
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "sample_rate": 1,
            "max_samples": 3,
            "max_payload_bytes": 1024,
        },
    ).json()["id"]
    record = span(kind="llm")
    assert (
        client.post("/v1/ingest/spans", headers=writer, json={"spans": [record]}).status_code == 200
    )
    body = {
        "policy_id": policy,
        "sample_id": str(uuid4()),
        "trace_id": record["trace_id"],
        "span_id": record["span_id"],
        "provider": "local",
        "input": "SYNTHETIC_CAPTURED_TEXT",
        "output": "<script>synthetic</script>",
        "provenance_ref": "owned-synthetic",
    }
    row = trace_rows(client, project, reader, env_id(app, project))[0]
    path = f"/v1/projects/{project}/traces/{row['id']}/spans/{record['span_id']}/content"
    return body, path, tmp_path / "encrypted"


def test_content_scope_encryption_audit_current_policy_and_expiry(
    api, tenant, content, monkeypatch
):
    client, admin, app = api
    project, writer, reader = tenant
    body, path, directory = content
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "false")
    assert client.post("/v1/ingest/trace-content", headers=writer, json=body).status_code == 403
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    for headers in (admin, reader):
        assert (
            client.post("/v1/ingest/trace-content", headers=headers, json=body).status_code == 403
        )
    response = client.post("/v1/ingest/trace-content", headers=writer, json=body)
    assert response.status_code == 201, response.text
    duplicate = client.post("/v1/ingest/trace-content", headers=writer, json=body)
    assert duplicate.status_code == 201 and duplicate.json()["status"] == "duplicate"
    assert (
        client.post(
            "/v1/ingest/trace-content", headers=writer, json={**body, "output": "changed"}
        ).status_code
        == 409
    )
    assert not any(
        b"SYNTHETIC_CAPTURED_TEXT" in p.read_bytes() for p in directory.rglob("*") if p.is_file()
    )
    report = detail(client, project, reader, env_id(app, project))
    assert report["spans"][0]["content_available"] is True
    assert "SYNTHETIC_CAPTURED_TEXT" not in json.dumps(report)
    assert (
        "SYNTHETIC_CAPTURED_TEXT"
        not in client.get(f"/v1/projects/{project}/export", headers=admin).text
    )
    for headers in (reader, writer):
        assert client.post(path, headers=headers, json={"purpose": "review"}).status_code == 403
    assert client.post(path, headers=admin, json={}).status_code == 422
    read = client.post(path, headers=admin, json={"purpose": "synthetic-acceptance"})
    assert read.status_code == 200 and read.json()["content"]["input"] == "SYNTHETIC_CAPTURED_TEXT"
    with app.state.sessions() as session:
        audit = session.scalar(
            select(AuditEvent).where(
                AuditEvent.project_id == UUID(project), AuditEvent.action == "trace_content_read"
            )
        )
        assert audit.details["purpose"] == "synthetic-acceptance"
        assert "SYNTHETIC_CAPTURED_TEXT" not in json.dumps(audit.details)
    # Scope changes and a global disable take effect on the next read.
    with app.state.sessions.begin() as session:
        policy = session.get(CapturePolicyRecord, UUID(body["policy_id"]))
        policy.policy = {**policy.policy, "workflows": ["different"]}
    assert client.post(path, headers=admin, json={"purpose": "review"}).status_code == 403
    with app.state.sessions.begin() as session:
        policy = session.get(CapturePolicyRecord, UUID(body["policy_id"]))
        policy.policy = {**policy.policy, "workflows": ["answer"]}
        session.execute(
            update(PayloadSample)
            .where(PayloadSample.id == UUID(body["sample_id"]))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert client.post(path, headers=admin, json={"purpose": "review"}).status_code == 410
    from cutback.server.worker import expire_payloads

    expire_payloads(app.state.sessions, app.state.artifact_store)
    assert not any(p.is_file() for p in directory.rglob("*"))
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
                .select_from(TraceContent)
                .where(TraceContent.project_id == UUID(project))
            )
            == 0
        )


def test_capture_conflicts_serialize_and_failed_insert_cleans_object(api, tenant, content):
    client, _, app = api
    project, writer, _ = tenant
    body, _, directory = content
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda i: client.post(
                    "/v1/ingest/trace-content",
                    headers=writer,
                    json={**body, "sample_id": str(uuid4()), "output": str(i)},
                ),
                range(2),
            )
        )
    assert sorted(r.status_code for r in responses) == [201, 409]
    with app.state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(TraceContent)
                .where(TraceContent.project_id == UUID(project))
            )
            == 1
        )
    second_span = span(span_id="2" * 16)
    assert (
        client.post("/v1/ingest/spans", headers=writer, json={"spans": [second_span]}).status_code
        == 200
    )
    before = {p for p in directory.rglob("*") if p.is_file()}

    def fail_flush(session, context, instances):
        if any(isinstance(row, TraceContent) for row in session.new):
            raise ValueError("SYNTHETIC_INSERT_FAILURE")

    sql_event.listen(Session, "before_flush", fail_flush)
    try:
        response = client.post(
            "/v1/ingest/trace-content",
            headers=writer,
            json={**body, "sample_id": str(uuid4()), "span_id": second_span["span_id"]},
        )
        assert response.status_code == 500
    finally:
        sql_event.remove(Session, "before_flush", fail_flush)
    assert {p for p in directory.rglob("*") if p.is_file()} == before


def test_comparison_aligns_only_retained_paths_without_claiming_quality(api, tenant):
    client, admin, app = api
    project, writer, reader = tenant
    root = span()
    first = [
        root,
        {
            **span(span_id="2" * 16, kind="tool", operation="lookup.order"),
            "parent_span_id": root["span_id"],
            "status": "error",
        },
    ]
    second = [{**s, "trace_id": "b" * 32, "status": "ok"} for s in first]
    second.append(
        {
            **span(span_id="3" * 16, kind="guardrail", operation="check.action"),
            "trace_id": "b" * 32,
            "parent_span_id": root["span_id"],
        }
    )
    assert (
        client.post("/v1/ingest/spans", headers=writer, json={"spans": first + second}).status_code
        == 200
    )
    rows = trace_rows(client, project, reader, env_id(app, project))
    one = next(r for r in rows if r["trace_id"] == root["trace_id"])
    two = next(r for r in rows if r["trace_id"] == "b" * 32)
    path = f"/v1/projects/{project}/traces/{one['id']}/compare/{two['id']}"
    response = client.get(path, headers=reader)
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["task_quality_comparison"] == "not_verified"
    assert [r["alignment"] for r in report["rows"]].count("matched_path_occurrence") == 2
    assert report["rows"][-1]["alignment"] == "right_only"
    assert client.get(path, headers=writer).status_code == 403
    other = client.post(
        "/v1/projects", headers=admin, json={"slug": "synthetic-compare-" + uuid4().hex}
    ).json()["id"]
    assert client.get(path.replace(project, other), headers=admin).status_code == 404
    from cutback.server.trace_comparison import align

    broken = [span(parent_span_id="f" * 16)]
    assert {r["alignment"] for r in align(broken, broken)} == {"left_only", "right_only"}
