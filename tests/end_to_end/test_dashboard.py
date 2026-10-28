"""Actual React build -> real authenticated HTTP API -> disposable PostgreSQL; no inference."""

import os
import runpy
import secrets
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from test_pipeline import server

from cutback.scanner import scan
from cutback.server.api import create_app


@pytest.mark.postgres
@pytest.mark.e2e
def test_dashboard_authenticated_accessibility(postgres_url, tmp_path, event, monkeypatch):
    if os.getenv("CUTBACK_TEST_BROWSER") != "true":
        pytest.skip("set CUTBACK_TEST_BROWSER=true after npm ci and playwright install chromium")
    from cutback.evals.artifacts import EncryptedFileStore

    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    monkeypatch.setenv("CUTBACK_EXPERIMENT_EXECUTION", "false")
    admin = secrets.token_urlsafe(48)
    app = create_app(database_url=postgres_url, admin_token=admin, allow_insecure_http=True)
    app.state.artifact_store = EncryptedFileStore(tmp_path / "encrypted-ui", os.urandom(32))
    root = Path(__file__).resolve().parents[2]
    with server(app) as endpoint, httpx.Client(base_url=endpoint, trust_env=False) as client:
        headers = {"Authorization": "Bearer " + admin}
        project = client.post(
            "/v1/projects", headers=headers, json={"slug": "synthetic-ui-" + uuid4().hex}
        ).json()["id"]
        writer = client.post(
            f"/v1/projects/{project}/keys", headers=headers, json={"role": "ingest"}
        ).json()["token"]
        (tmp_path / "main.py").write_text(
            "from openai import OpenAI\nc=OpenAI()\n"
            'c.responses.create(model="gpt-4.1-mini", input="synthetic")'
        )
        assert (
            client.post(
                f"/v1/projects/{project}/scans", headers=headers, json=scan(tmp_path)
            ).status_code
            == 201
        )
        unknown = event.model_copy(
            update={
                "event_id": uuid4(),
                "requested_model": "unknown-synthetic",
                "response_model": "unknown-synthetic",
                "traffic_kind": "application",
            }
        )
        assert (
            client.post(
                "/v1/ingest/events",
                headers={"Authorization": "Bearer " + writer},
                json={
                    "project_id": project,
                    "events": [event.model_dump(mode="json"), unknown.model_dump(mode="json")],
                },
            ).status_code
            == 200
        )
        fixture = runpy.run_path(str(root / "examples/workflow-tracing/sample.py"))
        spans, evaluations = fixture["demonstration"]()
        for path, data in (
            ("/v1/ingest/evaluations", evaluations),
            ("/v1/ingest/workflow-otel/v1/traces", spans),
        ):
            response = client.post(path, headers={"Authorization": "Bearer " + writer}, json=data)
            assert response.status_code == 200, response.text
        policy = client.post(
            f"/v1/projects/{project}/capture-policies",
            headers=headers,
            json={
                "enabled": True,
                "approval_ref": "synthetic-browser-trace",
                "rights_basis": "synthetic",
                "environments": ["staging"],
                "workflows": ["support.answer"],
                "allowed_providers": ["local"],
                "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                "sample_rate": 1,
            },
        ).json()["id"]
        original = spans["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        response = client.post(
            "/v1/ingest/trace-content",
            headers={"Authorization": "Bearer " + writer},
            json={
                "policy_id": policy,
                "sample_id": str(uuid4()),
                "trace_id": original["traceId"],
                "span_id": original["spanId"],
                "provider": "local",
                "input": "Browser synthetic input",
                "output": "<script>synthetic</script>",
                "provenance_ref": "owned-browser-fixture",
            },
        )
        assert response.status_code == 201, response.text
        from cutback.server.incidents import evaluate_alert
        from cutback.server.monitoring import epoch_ns, queue_online_intake, run_online_one

        anchor = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=10)
        rule = client.post(
            f"/v1/projects/{project}/online-rules",
            headers=headers,
            json={
                "version_id": str(uuid4()),
                "key": "browser-execution-health",
                "expected_revision": 0,
                "environment": "staging",
                "filter": {"workflow": "monitoring-browser"},
                "evaluator": {"kind": "execution_ok"},
                "starts_at": (anchor - timedelta(hours=1)).isoformat(),
                "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                "max_evaluations": 10,
                "approval_ref": "owned-monitoring-browser",
            },
        )
        assert rule.status_code == 201, rule.text
        alert = client.post(
            f"/v1/projects/{project}/alert-rules",
            headers=headers,
            json={
                "version_id": str(uuid4()),
                "key": "browser-execution-alert",
                "expected_revision": 0,
                "online_rule_id": rule.json()["id"],
                "window_seconds": 60,
                "grace_seconds": 0,
                "min_samples": 1,
                "threshold": "0.5",
                "cooldown_seconds": 0,
            },
        )
        assert alert.status_code == 201, alert.text
        response = client.post(
            "/v1/ingest/spans",
            headers={"Authorization": "Bearer " + writer},
            json={
                "spans": [
                    {
                        "trace_id": uuid4().hex,
                        "span_id": uuid4().hex[:16],
                        "operation": "owned-monitoring-check",
                        "service": "owned-browser",
                        "workflow": "monitoring-browser",
                        "kind": "guardrail",
                        "status": "error",
                        "started_ns": epoch_ns(anchor + timedelta(seconds=1)),
                        "ended_ns": epoch_ns(anchor + timedelta(seconds=2)),
                    }
                ]
            },
        )
        assert response.status_code == 200, response.text
        queue_online_intake(app.state.sessions, enabled=True, only_project_id=UUID(project))
        assert run_online_one(app.state.sessions, enabled=True)["status"] == "failed"
        evaluate_alert(
            app.state.sessions, UUID(alert.json()["id"]), now=anchor + timedelta(seconds=61)
        )
        result = subprocess.run(
            [shutil.which("npm.cmd") or shutil.which("npm") or "npm", "test"],
            cwd=root / "web",
            env={
                **os.environ,
                "CUTBACK_UI_URL": endpoint,
                "CUTBACK_UI_KEY": admin,
                "CUTBACK_UI_PROJECT": project,
            },
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
