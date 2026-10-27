"""Actual React build -> real authenticated HTTP API -> disposable PostgreSQL; no inference."""

import os
import secrets
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

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
