import os
import secrets
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from test_pipeline import server

from cutback.demo_campaigns import seed_campaign
from cutback.evals.artifacts import EncryptedFileStore
from cutback.server.api import create_app


@pytest.mark.postgres
def test_source_bound_native_campaign_and_authenticated_browser(
    postgres_url, tmp_path, monkeypatch
):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    admin = secrets.token_urlsafe(48)
    app = create_app(database_url=postgres_url, admin_token=admin, allow_insecure_http=True)
    app.state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    with (
        server(app) as endpoint,
        httpx.Client(
            base_url=endpoint, headers={"Authorization": "Bearer " + admin}, trust_env=False
        ) as control,
    ):
        project = control.post(
            "/v1/projects", json={"slug": "synthetic-campaign-" + uuid4().hex}
        ).json()["id"]
        writer = control.post(f"/v1/projects/{project}/keys", json={"role": "ingest"}).json()[
            "token"
        ]
        result = seed_campaign(app, control, project, writer, tmp_path)
        report = result["report"]
        assert report["status"] == "COMPLETED" and result["paid_provider_calls"] == 0
        assert report["requests"] == report["known_cost_requests"] == result["native_calls"]
        assert len(report["candidates"]) == 3 and len(report["runs"]) == 4
        assert report["specification"]["version"] == "cutback.improvement-campaign.v2"
        assert report["summary"]["static_stage"] == "completed"
        assert report["source_profile_sha256"]
        assert report["summary"]["decision"] == "synthetic_confirmation_passed_real_data_required"
        assert not report["summary"]["deployment_authorized"]
        assert result["source_diff"]["status"] == "DRY_RUN_ONLY"
        assert "Answer A only." in result["source_diff"]["diff"]
        assert result["source_diff"]["rollback_diff"]
        if os.getenv("CUTBACK_TEST_BROWSER") == "true":
            completed = subprocess.run(
                [
                    shutil.which("npm.cmd") or shutil.which("npm") or "npm",
                    "test",
                    "--",
                    "campaigns.spec.ts",
                ],
                cwd=Path(__file__).resolve().parents[2] / "web",
                env={
                    **os.environ,
                    "CUTBACK_UI_URL": endpoint,
                    "CUTBACK_UI_KEY": admin,
                    "CUTBACK_UI_PROJECT": project,
                    "CUTBACK_CAMPAIGN_SAMPLE": "true",
                    "CUTBACK_CAMPAIGN_ID": result["campaign_id"],
                    "CUTBACK_CAMPAIGN_TEMPLATE": result["template_plan_id"],
                    "CUTBACK_CAMPAIGN_SNAPSHOT": result["baseline_snapshot_id"],
                },
                capture_output=True,
                text=True,
                timeout=180,
            )
            assert completed.returncode == 0, completed.stdout + completed.stderr
