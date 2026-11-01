"""Independent application completes one search through real HTTP, PG and checked rollback."""

import os
import subprocess
from pathlib import Path

import pytest
from support_browser import browser_command
from support_http import owned_collector

from cutback.demo_policy_program import run_sample


@pytest.mark.postgres
def test_independent_policy_program_confirms_one_checked_source_change(
    api, tenant, tmp_path, monkeypatch
):
    from cutback.evals.artifacts import EncryptedFileStore

    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    with owned_collector(api[2]) as endpoint:
        receipt = run_sample(
            api[2],
            api[0],
            endpoint,
            api[1]["Authorization"][7:],
            tenant[0],
            tenant[1]["Authorization"][7:],
            tmp_path,
        )
        if os.getenv("CUTBACK_TEST_BROWSER") == "true":
            from cutback.demo import install_demo_routes

            detail = (
                api[0]
                .get(
                    f"/v1/projects/{tenant[0]}/source-policy-programs/{receipt['program_id']}",
                    headers=api[1],
                )
                .json()
            )
            confirmation = next(
                row["id"] for row in detail["runs"] if row["phase"] == "confirmation"
            )
            from cutback.core.workspace import read_private

            artifact = read_private(Path(receipt["checked_handoff"]["workspace"]) / "proposal.json")
            stages = []
            for row in detail["runs"]:
                experiment = (
                    api[0]
                    .get(f"/v1/projects/{tenant[0]}/experiments/{row['id']}", headers=api[1])
                    .json()
                )
                stages.append(
                    {
                        "stage": row["phase"],
                        "run_id": row["id"],
                        "cases": experiment["results"]["sample_n"],
                    }
                )
            install_demo_routes(
                api[2],
                {
                    "available": True,
                    "project_id": tenant[0],
                    "comparison_run_id": confirmation,
                    "technical_run_id": confirmation,
                    "source_diff": {
                        "diff": artifact["diff"],
                        "rollback_diff": artifact["rollback_diff"],
                    },
                    "stages": stages,
                    "disclosure": "Independent synthetic application; no paid provider calls.",
                    "source_policy_sample": receipt,
                },
                api[1]["Authorization"][7:],
                endpoint,
            )
            root = Path(__file__).resolve().parents[2]
            result = subprocess.run(
                browser_command(root / "web", "source-policy-sample.spec.ts"),
                cwd=root / "web",
                env={
                    **os.environ,
                    "CUTBACK_UI_URL": endpoint,
                    "CUTBACK_UI_PROJECT": tenant[0],
                    "CUTBACK_UI_KEY": api[1]["Authorization"][7:],
                    "CUTBACK_SOURCE_POLICY_SAMPLE": receipt["program_id"],
                    "CUTBACK_UI_OUTPUT_DIR": str(tmp_path / "private-sample-browser"),
                },
                capture_output=True,
                text=True,
                timeout=180,
            )
            safe = (result.stdout + result.stderr).replace(api[1]["Authorization"][7:], "<omitted>")
            assert result.returncode == 0, safe
    assert receipt["status"] == "COMPLETED" and receipt["paid_provider_calls"] == 0
    assert receipt["compatible_data_selected_automatically"] is True
    assert receipt["native_sdk_calls"] == receipt["known_cost_requests"]
    assert receipt["phases"] == [
        "static_validation",
        "training",
        "routed_validation",
        "confirmation",
    ]
    assert receipt["checked_handoff"]["status"] == "REVIEWED"
    assert receipt["checked_handoff"]["both_application_trees_checked"] is True
    assert receipt["checked_handoff"]["byte_exact_rollback_checked"] is True
    assert receipt["original_application_changed"] is False
    assert receipt["real_model_quality_verified"] is False
    assert receipt["production_savings_usd"] is None
