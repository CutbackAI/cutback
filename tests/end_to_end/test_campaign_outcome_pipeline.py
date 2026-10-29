"""Independent native application, ordinary agent preparation and later outcomes."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from cutback.demo_campaign_outcomes import run_sample
from cutback.evals.artifacts import EncryptedFileStore

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("watch,modern_model", [(False, False), (True, False), (False, True)])
def test_ordinary_campaign_checks_uploaded_diff_and_later_rollback_review(
    api, tenant, tmp_path, monkeypatch, watch, modern_model
):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    app = api[2]
    app.state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    result = run_sample(
        app,
        api[0],
        "http://127.0.0.1",
        api[1]["Authorization"][7:],
        tenant[0],
        tenant[1]["Authorization"][7:],
        tmp_path,
        watch=watch,
        modern_model=modern_model,
    )
    assert result["report"]["status"] == "COMPLETED"
    assert (
        result["report"]["requests"]
        == result["report"]["known_cost_requests"]
        == result["native_calls"]
    )
    assert result["application_check_native_calls"] == 2 and result["paid_provider_calls"] == 0
    if modern_model:
        from uuid import UUID

        from sqlalchemy import select

        from cutback.core.native_models import NativeModelContract
        from cutback.server.db import ExperimentPlanRecord
        from cutback.server.execution_db import ExecutionCall

        assert result["baseline_model"] == "gpt-6-luna"
        with app.state.sessions() as session:
            plans = session.scalars(
                select(ExperimentPlanRecord).where(
                    ExperimentPlanRecord.project_id == UUID(tenant[0])
                )
            ).all()
            assert plans
            modern_plans = 0
            modern_proposers = 0
            for plan in plans:
                baseline = plan.specification["public_specification"]["baseline"]
                if baseline["model"] != "gpt-6-luna":
                    assert baseline["model"] == "gpt-4.1-mini"  # Separate proposer request.
                    assert "native_contract" not in baseline
                    continue
                modern_plans += 1
                contract = baseline["native_contract"]
                assert (
                    contract["price_sha256"] == NativeModelContract.current_luna_text().price_sha256
                )
                if (
                    plan.specification["public_specification"].get("operation")
                    == "candidate-generation-v1"
                ):
                    assert (
                        contract["reasoning_effort"] == "none"
                        and contract["service_tier"] == "default"
                    )
                    modern_proposers += 1
                else:
                    assert (
                        contract["reasoning_effort"] == "medium"
                        and contract["service_tier"] == "auto"
                    )
            assert modern_plans >= 3
            assert modern_proposers == 0  # Static cap reduction passed; no generation is needed.
            calls = session.scalars(
                select(ExecutionCall).where(
                    ExecutionCall.project_id == UUID(tenant[0]), ExecutionCall.model == "gpt-6-luna"
                )
            ).all()
            assert calls and all(
                call.price_sha256 == NativeModelContract.current_luna_text().price_sha256
                for call in calls
            )
            assert not any(call.purpose == "optimization" for call in calls)
    assert result["application_check_discovery"]["executed_during_discovery"] == 0
    assert len(result["application_check_discovery"]["commands"]) == 1
    assert result["review"]["status"] == "READY_FOR_PR_REVIEW"
    assert result["outcome_review"]["next_action"] == "REVIEW_ROLLBACK"
    evidence = result["outcome_review"]["receipt"]["evidence"]
    assert len(evidence["run_costs"]) == (2 if modern_model else 4) and evidence["change_id"]
    assert evidence["report"]["declared_optimization_total_usd"] is not None
    assert evidence["report"]["production_savings_usd"] is None
    assert api[1]["Authorization"][7:] not in json.dumps(result)
    if modern_model and os.getenv("CUTBACK_TEST_BROWSER") == "true":
        from test_pipeline import server

        token = api[1]["Authorization"][7:]
        with server(app) as endpoint:
            browser = subprocess.run(
                [
                    shutil.which("npm.cmd") or shutil.which("npm") or "npm",
                    "test",
                    "--",
                    "native-modern.spec.ts",
                ],
                cwd=Path(__file__).resolve().parents[2] / "web",
                env={
                    **os.environ,
                    "CUTBACK_UI_URL": endpoint,
                    "CUTBACK_UI_KEY": token,
                    "CUTBACK_UI_PROJECT": tenant[0],
                    "CUTBACK_NATIVE_MODERN": "true",
                    "CUTBACK_NATIVE_CAMPAIGN": result["campaign_id"],
                },
                capture_output=True,
                text=True,
                timeout=120,
            )
        assert browser.returncode == 0, (browser.stdout + browser.stderr).replace(
            token, "<credential-omitted>"
        )

    if watch:
        automatic = result["automatic_observation"]
        assert automatic["receipt"]["status"] == "EXHAUSTED"
        assert automatic["receipt"]["windows_processed"] == 1
        assert len(automatic["windows"]["items"]) == 1
        assert automatic["windows"]["items"][0]["evidence"]["next_action"] == "REVIEW_ROLLBACK"
        assert automatic["provider_calls"] == 0 and not automatic["traffic_changed"]
