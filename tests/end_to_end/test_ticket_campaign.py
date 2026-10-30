"""A varied independent task through agent, native ledger, encrypted cases and source checks."""

import json
import os
import subprocess
from pathlib import Path

import pytest
from support_browser import browser_command

from cutback.demo_campaign_outcomes import run_sample
from cutback.evals.artifacts import EncryptedFileStore

pytestmark = pytest.mark.postgres


def test_three_label_ticket_campaign_rejects_constant_answers_and_retains_authorized_paired_cases(
    api, tenant, tmp_path, monkeypatch
):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    result = run_sample(
        api[2],
        api[0],
        "http://127.0.0.1",
        api[1]["Authorization"][7:],
        tenant[0],
        tenant[1]["Authorization"][7:],
        tmp_path,
        ticket_router=True,
    )
    report = result["report"]
    assert result["scenario"] == "synthetic-ticket-routing.v1"
    assert result["reference_labels"] == 3 and result["retained_cases_authorized"] is True
    assert result["paid_provider_calls"] == 0 and result["application_check_native_calls"] == 6
    assert report["status"] == "COMPLETED" and report["billing_complete"]
    assert report["requests"] == result["native_calls"]
    assert result["review"]["status"] == "READY_FOR_PR_REVIEW"
    assert result["outcome_review"]["next_action"] == "REVIEW_ROLLBACK"
    assert (
        report["summary"]["search_decision"]
        == "static_alternative_failed_use_reserved_instruction_search"
    )
    first = next(row for row in report["runs"] if row["phase"] == "validation")
    path = f"/v1/projects/{tenant[0]}/experiments/{first['run_id']}/cases/paired"
    assert api[0].post(path, headers=tenant[2], json={"purpose": "owned-review"}).status_code == 403
    response = api[0].post(
        path, headers=api[1], json={"purpose": "owned-review"}, params={"limit": 10}
    )
    assert response.status_code == 200, response.text
    captured = response.json()["cases"]
    assert captured and any(item["case"]["gold"] != "billing" for item in captured)
    assert any(
        output["output"] != item["case"]["gold"] for item in captured for output in item["outputs"]
    )
    assert all("Ticket from synthetic team" not in json.dumps(row) for row in report["runs"])
    assert api[1]["Authorization"][7:] not in json.dumps(report)


def test_no_key_guided_ticket_sample_and_authenticated_paired_case_browser(
    api, tmp_path, monkeypatch
):
    if os.getenv("CUTBACK_TEST_BROWSER") != "true":
        pytest.skip("set CUTBACK_TEST_BROWSER=true for the no-key authenticated ticket journey")
    from test_pipeline import server

    from cutback.demo import seed_support_desk

    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    app = api[2]
    app.state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    with server(app) as endpoint:
        manifest = seed_support_desk(
            app, endpoint, api[1]["Authorization"][7:], tmp_path / "workspace", ticket_router=True
        )
        assert manifest["ticket_campaign_sample"]["reference_labels"] == 3
        assert manifest["ticket_campaign_sample"]["declared_intent_families"] == 60
        assert manifest["ticket_campaign_sample"]["paid_provider_calls"] == 0
        result = subprocess.run(
            browser_command(Path(__file__).resolve().parents[2] / "web", "ticket-campaign.spec.ts"),
            cwd=Path(__file__).resolve().parents[2] / "web",
            env={
                **os.environ,
                "CUTBACK_UI_URL": endpoint,
                "CUTBACK_TICKET_SAMPLE": "true",
                "CUTBACK_UI_OUTPUT_DIR": str(tmp_path / "ticket-browser"),
            },
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert result.returncode == 0, result.stdout + result.stderr
