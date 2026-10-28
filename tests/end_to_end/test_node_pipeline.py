"""Installed native Node SDKs -> actual HTTP collector -> migrated PostgreSQL -> report."""

import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from test_pipeline import server

from cutback.server.api import create_app


@pytest.mark.postgres
@pytest.mark.e2e
@pytest.mark.parametrize("modern_cache", [False, True])
def test_node_synthetic_provider_pipeline(postgres_url, modern_cache):
    root = Path(__file__).resolve().parents[2]
    package = root / "packages/js-sdk"
    if not shutil.which("node") or not (package / "node_modules/openai").exists():
        pytest.skip("npm ci --ignore-scripts in packages/js-sdk and Node 24 required")
    admin = secrets.token_urlsafe(48)
    app = create_app(database_url=postgres_url, admin_token=admin, allow_insecure_http=True)
    with server(app) as endpoint, httpx.Client(base_url=endpoint, trust_env=False) as client:
        headers = {"Authorization": "Bearer " + admin}
        project = client.post(
            "/v1/projects", headers=headers, json={"slug": "node-test-" + uuid4().hex}
        ).json()["id"]
        key = client.post(
            f"/v1/projects/{project}/keys", headers=headers, json={"role": "ingest"}
        ).json()["token"]
        result = subprocess.run(
            [shutil.which("node"), "examples/synthetic-pipeline.mjs"]
            + (["--modern-cache"] if modern_cache else []),
            cwd=package,
            env={
                **os.environ,
                "CUTBACK_ENDPOINT": endpoint,
                "CUTBACK_PROJECT_ID": project,
                "CUTBACK_PROJECT_TOKEN": key,
                "NODE_USE_ENV_PROXY": "1",
                "HTTP_PROXY": "http://127.0.0.1:1",
                "HTTPS_PROXY": "http://127.0.0.1:1",
                "NO_PROXY": "",
            },
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["counters"]["exported"] == 2
        response = client.get(f"/v1/projects/{project}/usage", headers=headers)
        assert response.status_code == 200
        report = response.json()
        assert report["summary"]["observed_attempts"] == 2
        assert report["summary"]["unpriced_requests"] == 0
        assert report["summary"]["modeled_cost_usd"] is not None
        from decimal import Decimal

        expected_cost = "0.00005007" if modern_cache else "0.0000604"
        assert Decimal(report["summary"]["modeled_cost_usd"]) == Decimal(expected_cost)
        health = client.get(f"/v1/projects/{project}/health", headers=headers).json()
        assert health["instances"][0]["counters"]["exported"] == 2
