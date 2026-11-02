"""Exercise the actual sample setup without granting any collector or model request."""

from uuid import uuid4

import httpx
import pytest

from cutback.application_check_discovery import discover
from cutback.application_checks import ApplicationCheckContract, snapshot
from cutback.application_handoff import declared_coverage_candidate
from cutback.application_workspaces import materialize
from cutback.core.inventory import workflow_identity
from cutback.demo_campaign_outcomes import run_sample
from cutback.scanner import scan


@pytest.mark.parametrize(
    "ticket_router,modern_model", [(False, False), (True, False), (False, True)]
)
def test_sample_freezes_real_workflow_declarations_before_control_authority(
    tmp_path, monkeypatch, ticket_router, modern_model
):
    class StopBeforeAuthority(Exception):
        pass

    prepared = []

    def stop(root, *args, **kwargs):
        prepared.append(root)
        raise StopBeforeAuthority

    def no_request(request):
        raise AssertionError("SAMPLE_SETUP_MUST_NOT_REQUEST_COLLECTOR_OR_PROVIDER")

    monkeypatch.setattr("cutback.agent.prepare", stop)
    with httpx.Client(transport=httpx.MockTransport(no_request), trust_env=False) as client:
        with pytest.raises(StopBeforeAuthority):
            run_sample(
                None,
                client,
                "http://127.0.0.1",
                "synthetic-control",
                str(uuid4()),
                "synthetic-ingestion",
                tmp_path,
                ticket_router=ticket_router,
                modern_model=modern_model,
            )
    assert len(prepared) == 1
    root = prepared[0]
    before = snapshot(root)
    source = (root / "app.py").read_bytes()
    proposed = source.replace(b"max_output_tokens=256", b"max_output_tokens=128")
    assert proposed != source
    pair = tmp_path / "independent-source-pair"
    materialize(root, pair, before, {"app.py": proposed})
    report = discover(pair / "base", pair / "proposed")
    contract = ApplicationCheckContract.model_validate(report["contract"])
    expected = {
        workflow_identity(entry["file"], entry["symbol"])[0]
        for site in scan(root)["call_sites"]
        if site["source_file"] == "app.py"
        for entry in site["entry_points"]
        or [{"file": site["source_file"], "symbol": site["source_symbol"]}]
    }
    assert expected
    assert report["impact"]["mode"] == "all_commands_affected_first"
    assert declared_coverage_candidate(report, contract.commands, expected) == {
        key: ["owned-native-application"] for key in expected
    }
    assert report["application_commands_executed"] == report["provider_calls_authorized"] == 0
    assert snapshot(root) == before
