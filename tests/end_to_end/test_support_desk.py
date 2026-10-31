"""Real PostgreSQL/HTTP/SDK/worker acceptance for the independently runnable sample."""

import json
import os
import secrets
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from support_browser import browser_command
from test_pipeline import server

from cutback.demo import seed_support_desk
from cutback.evals.artifacts import EncryptedFileStore
from cutback.server.api import create_app


@pytest.mark.postgres
@pytest.mark.parametrize(
    "workload_portfolio", [False, True], ids=["standard", "workload-portfolio"]
)
def test_support_desk_decisions_stages_encrypted_cases_and_browser(
    postgres_url, tmp_path, monkeypatch, workload_portfolio
):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    admin = secrets.token_urlsafe(48)
    app = create_app(database_url=postgres_url, admin_token=admin, allow_insecure_http=True)
    store_path = tmp_path / "encrypted"
    app.state.artifact_store = EncryptedFileStore(store_path, os.urandom(32))
    with (
        server(app) as endpoint,
        httpx.Client(
            base_url=endpoint, headers={"Authorization": "Bearer " + admin}, trust_env=False
        ) as client,
    ):
        manifest = seed_support_desk(
            app, endpoint, admin, tmp_path / "workspace", workload_portfolio=workload_portfolio
        )
        base = "/v1/projects/" + manifest["project_id"]
        run_id = manifest["comparison_run_id"]
        report = client.get(base + "/experiments/" + run_id).json()
        decisions = {r["candidate"]["key"]: r for r in report["results"]["recommendations"]}
        assert decisions["compact"]["quality_gate_passed"]
        assert not decisions["unsafe"]["quality_gate_passed"]
        assert "priority" in decisions["unsafe"]["failed_criteria"]
        unsafe_rec = next(
            item
            for item in client.get(base + "/recommendations").json()
            if item["run_id"] == run_id and item["evidence"]["candidate"]["key"] == "unsafe"
        )
        assert (
            client.post(
                base + f"/recommendations/{unsafe_rec['id']}/review",
                json={"decision": "accepted_for_staging", "reason": "must-not-bypass-priority"},
            ).status_code
            == 409
        )
        assert manifest["paid_provider_calls"] == 0
        usage = client.get(base + "/usage?traffic_kind=application&group_by=workflow").json()
        assert usage["summary"]["observed_attempts"] == 19 + 8 * workload_portfolio
        assert len(usage["groups"]) == 3 + workload_portfolio
        inventory = client.get(base + "/inventory").json()
        assert (
            sum(row["observed_attempts"] for row in inventory["runtime_by_call_site"].values())
            == 19 + 8 * workload_portfolio
        )
        assert client.get(base + "/changes").json()
        assert [s["stage"] for s in manifest["stages"]] == ["smoke", "pilot", "full"]
        assert manifest["source_diff"]["diff"] and manifest["source_diff"]["rollback_diff"]
        assert "Synthetic ticket" not in json.dumps(report)
        contents = client.post(
            base + f"/experiments/{run_id}/cases/content", json={"purpose": "synthetic-acceptance"}
        )
        assert contents.status_code == 200, contents.text
        assert contents.json()["cases"]
        assert "Synthetic ticket" in json.dumps(contents.json())
        assert not any(
            b"Synthetic ticket" in p.read_bytes() for p in store_path.rglob("*") if p.is_file()
        )
        viewer = client.post(base + "/keys", json={"role": "viewer"}).json()["token"]
        assert (
            client.post(
                base + f"/experiments/{run_id}/cases/content",
                headers={"Authorization": "Bearer " + viewer},
                json={"purpose": "synthetic-acceptance"},
            ).status_code
            == 403
        )
        other = client.post("/v1/projects", json={"slug": "other-" + uuid4().hex}).json()["id"]
        assert (
            client.post(
                f"/v1/projects/{other}/experiments/{run_id}/cases/content",
                json={"purpose": "synthetic-acceptance"},
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/v1/demo/connect",
                headers={"Origin": "https://hostile.example", "X-Cutback-Demo": "connect"},
                json={},
            ).status_code
            == 403
        )
        assert client.post("/v1/demo/connect", json={}).status_code == 403
        if os.getenv("CUTBACK_TEST_BROWSER") == "true":
            result = subprocess.run(
                browser_command(
                    Path(__file__).resolve().parents[2] / "web",
                    "support-demo.spec.ts",
                    "execution.spec.ts",
                    *(["workload-portfolio-demo.spec.ts"] if workload_portfolio else []),
                ),
                cwd=Path(__file__).resolve().parents[2] / "web",
                env={
                    **os.environ,
                    "CUTBACK_UI_URL": endpoint,
                    "CUTBACK_SUPPORT_DEMO": "true",
                    "CUTBACK_WORKLOAD_DEMO_BROWSER": "true" if workload_portfolio else "false",
                    "CUTBACK_UI_KEY": admin,
                    "CUTBACK_PORTFOLIO_VIEWER": viewer,
                    "CUTBACK_UI_PROJECT": manifest["project_id"],
                },
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode:
                safe = (
                    (result.stdout + result.stderr)
                    .replace(admin, "<credential-omitted>")
                    .replace(viewer, "<credential-omitted>")
                )
                pytest.fail(safe, pytrace=False)
        from cutback.server.db import CapturePolicyRecord, ExperimentPlanRecord, ExperimentRunRecord

        with app.state.sessions.begin() as session:
            # The guided sample contains several independent policies. Revoke
            # the one authorizing these exact case outputs, not an unordered
            # first policy that may belong to a different demonstration.
            run = session.get(ExperimentRunRecord, UUID(run_id))
            assert run is not None and run.project_id == UUID(manifest["project_id"])
            plan = session.get(ExperimentPlanRecord, run.plan_id)
            policy = session.get(CapturePolicyRecord, UUID(plan.specification["policy_id"]))
            assert policy is not None and policy.project_id == run.project_id and policy.enabled
            policy.enabled = False
        assert (
            client.post(
                base + f"/experiments/{run_id}/cases/content",
                json={"purpose": "synthetic-acceptance"},
            ).status_code
            == 403
        )
        assert (
            client.delete(base, params={"confirm_slug": manifest["project_slug"]}).status_code
            == 200
        )
        assert not list((store_path / manifest["project_id"]).glob("*"))
