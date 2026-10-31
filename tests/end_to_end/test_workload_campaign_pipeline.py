"""Actual observed source → one campaign → confirmed cache diff and rollback."""

import os
import secrets
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from support_browser import browser_command
from support_http import owned_collector
from test_pipeline import server

from cutback.demo_workload_campaign import seed_workload_campaign
from cutback.demo_workload_portfolio import seed_workload_portfolio
from cutback.evals.artifacts import EncryptedFileStore
from cutback.server.api import create_app
from cutback.server.campaign_db import CampaignRecord, CampaignRun
from cutback.server.db import ExperimentRunRecord


@pytest.mark.postgres
def test_observed_workload_campaign_and_authenticated_dashboard(
    postgres_url, tmp_path, monkeypatch
):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    admin = secrets.token_urlsafe(48)
    app = create_app(database_url=postgres_url, admin_token=admin, allow_insecure_http=True)
    app.state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    with (
        server(app) as endpoint,
        httpx.Client(
            base_url=endpoint,
            headers={"Authorization": "Bearer " + admin},
            trust_env=False,
            timeout=30,
        ) as control,
    ):
        project = control.post(
            "/v1/projects", json={"slug": "owned-workload-campaign-" + uuid4().hex}
        ).json()["id"]
        base = f"/v1/projects/{project}"
        writer = control.post(base + "/keys", json={"role": "ingest"}).json()["token"]
        result = seed_workload_campaign(app, control, project, writer, tmp_path / "new-workspace")
        report = result["report"]
        assert report["status"] == "COMPLETED" and report["billing_complete"]
        assert report["specification"]["version"] == "cutback.improvement-campaign.v4"
        assert report["specification"]["generator"]["transport"] == "scripted_test_only"
        assert len(report["runs"]) == 2 and all(r["phase"] != "proposal" for r in report["runs"])
        assert result["observed_native_calls"] == 8 and result["modeled_potential_hits"] == 3
        assert result["rollback_verified"] and result["paid_provider_calls"] == 0
        assert result["application_checks"]["receipt"]["all_passed"]
        assert "_cutback_cached_responses" in result["source_diff"]["diff"]
        assert not result["runtime_cache_activated"] and not result["real_model_quality_verified"]
        if os.getenv("CUTBACK_TEST_BROWSER") == "true":
            viewer = control.post(base + "/keys", json={"role": "viewer"}).json()["token"]
            web = Path(__file__).resolve().parents[2] / "web"
            completed = subprocess.run(
                browser_command(web, "workload-campaign.spec.ts"),
                cwd=web,
                env={
                    **os.environ,
                    "CUTBACK_UI_URL": endpoint,
                    "CUTBACK_UI_KEY": admin,
                    "CUTBACK_UI_PROJECT": project,
                    "CUTBACK_WORKLOAD_CAMPAIGN": "true",
                    "CUTBACK_CAMPAIGN_ID": result["campaign_id"],
                    "CUTBACK_CAMPAIGN_TEMPLATE": result["template_plan_id"],
                    "CUTBACK_CAMPAIGN_SNAPSHOT": result["baseline_snapshot_id"],
                    "CUTBACK_WORKLOAD_CAMPAIGN_VIEWER": viewer,
                    "CUTBACK_DEMO_VIDEO": "false",
                    "CUTBACK_UI_OUTPUT_DIR": str(tmp_path / "browser"),
                },
                capture_output=True,
                text=True,
                timeout=180,
            )
            safe = (
                (completed.stdout + completed.stderr)
                .replace(admin, "<omitted>")
                .replace(viewer, "<omitted>")
            )
            assert completed.returncode == 0, safe


@pytest.mark.postgres
def test_reimported_sample_families_cannot_claim_fresh_campaign_confirmation(
    api, tenant, tmp_path, monkeypatch
):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    from cutback.server import campaign_worker
    from cutback.server.dataset_lineage import LineageViolation

    lineage_denials = []
    ordinary_child = campaign_worker.child_run

    def observe_child(*args, **kwargs):
        try:
            return ordinary_child(*args, **kwargs)
        except LineageViolation as exc:
            lineage_denials.append(str(exc))
            raise

    monkeypatch.setattr(campaign_worker, "child_run", observe_child)
    with (
        owned_collector(api[2]) as endpoint,
        httpx.Client(base_url=endpoint, headers=api[1], trust_env=False, timeout=30) as control,
    ):
        initial = seed_workload_portfolio(
            api[2], control, tenant[0], tmp_path, tenant[1]["Authorization"][7:]
        )
        assert initial["rollback_verified"]
        with pytest.raises(ValueError, match="SYNTHETIC_WORKLOAD_CAMPAIGN_DID_NOT_COMPLETE"):
            seed_workload_campaign(
                api[2],
                control,
                tenant[0],
                tenant[1]["Authorization"][7:],
                tmp_path / "reimported-workspace",
            )
    with api[2].state.sessions() as session:
        parent = session.query(CampaignRecord).filter_by(project_id=UUID(tenant[0])).one()
        assert parent.status == "FAILED"
        assert parent.summary["code"] == "CAMPAIGN_AUTHORITY_INPUT_OR_TRANSITION_FAILED_NO_REPLAY"
        assert set(lineage_denials) & {
            "INDEPENDENT_TEST_GROUPS_ALREADY_RESERVED",
            "TEST_FAMILIES_PREVIOUSLY_EXPOSED_IN_THIS_PROJECT",
            "DATASET_GLOBAL_FAMILY_SPANS_TEST_AND_TRAIN_OR_VALIDATION",
        }, lineage_denials
        links = session.query(CampaignRun).filter_by(campaign_id=parent.id).all()
        assert not any(
            session.get(ExperimentRunRecord, link.run_id).status == "COMPLETED"
            and link.phase == "confirmation"
            for link in links
        )
