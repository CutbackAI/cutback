"""Actual pinned SDK -> owned transport -> HTTP -> PostgreSQL -> frozen report/UI."""

import os
import secrets
import subprocess
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from support_browser import browser_command
from test_pipeline import server

from cutback.demo_economics import seed_task_economics
from cutback.server.api import create_app


@pytest.mark.postgres
def test_owned_native_task_sample_and_browser(postgres_url):
    admin = secrets.token_urlsafe(48)
    app = create_app(database_url=postgres_url, admin_token=admin, allow_insecure_http=True)
    with (
        server(app) as endpoint,
        httpx.Client(
            base_url=endpoint,
            headers={"Authorization": "Bearer " + admin},
            trust_env=False,
        ) as client,
    ):
        project = client.post(
            "/v1/projects", json={"slug": "synthetic-economics-" + uuid4().hex}
        ).json()["id"]
        base = f"/v1/projects/{project}"
        writer = client.post(base + "/keys", json={"role": "ingest"}).json()["token"]
        result = seed_task_economics(client, endpoint, project, writer)
        assert result["native_calls"] == 7 and result["tasks"] == 5
        usage = client.get(base + "/usage").json()["summary"]
        assert usage["observed_attempts"] == 7 and usage["priced_requests"] == 7
        env = client.get(base + "/environments").json()["items"][0]["id"]
        query = {
            "environment_id": env,
            "workflow": result["workflow"],
            "start": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "end": datetime.now(UTC).isoformat(),
        }
        reports = {}
        for release, count in (
            ("synthetic-reference", 2),
            ("synthetic-cheap-but-worse", 2),
            ("synthetic-incomplete", 1),
        ):
            response = client.get(
                base + "/task-economics",
                params={**query, "release": release, "expected_tasks": count},
            )
            assert response.status_code == 200, response.text
            reports[release] = response.json()["summary"]
            if release != "synthetic-incomplete":
                assert reports[release]["window_cost_complete_under_declaration"], [
                    {"integrity": item["trace_integrity"], "economics": item["economics"]}
                    for item in response.json()["items"]
                ]
        reference, worse, incomplete = (
            reports[key]
            for key in ("synthetic-reference", "synthetic-cheap-but-worse", "synthetic-incomplete")
        )
        assert reference["declared_successes"] == 2 and worse["declared_failures"] == 1
        assert Decimal(worse["declared_window_cost_per_success_usd"]) < Decimal(
            reference["declared_window_cost_per_success_usd"]
        )
        assert incomplete["declared_window_cost_per_success_usd"] is None
        if os.getenv("CUTBACK_TEST_BROWSER") == "true":
            result = subprocess.run(
                browser_command(
                    Path(__file__).resolve().parents[2] / "web", "task-economics.spec.ts"
                ),
                cwd=Path(__file__).resolve().parents[2] / "web",
                env={
                    **os.environ,
                    "CUTBACK_UI_URL": endpoint,
                    "CUTBACK_UI_KEY": admin,
                    "CUTBACK_UI_PROJECT": project,
                    "CUTBACK_TASK_SAMPLE": "true",
                },
                capture_output=True,
                text=True,
                timeout=120,
            )
            assert result.returncode == 0, result.stdout + result.stderr
