"""Source workload guards reject forged, stale and conflicting agent assessments."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from integration.test_agent_cache_workload import committed_application
from unit.test_cache_workload_contract import assessment

from cutback.agent import analyze, load_session
from cutback.agent_cache_workload import assess
from cutback.core.cache_workload import CACHE_WORKLOAD_CONTRACT
from cutback.core.request_profiles import fingerprint


def contract():
    return {
        "freshness_ref": "owned-context",
        "ttl_seconds": 300,
        "content_retention_authorized": True,
        "immutable_or_safely_invalidated": True,
        "operation_cost_upper_usd": "0.001",
        "operation_cost_basis_ref": "owned-bound",
    }


def control_for(state, *, mutate=None, change=None):
    identifier, calls = str(uuid4()), []

    def control(method, path, body=None):
        calls.append((method, path))
        if path.endswith("/health/ready"):
            return {"cache_workload_assessment_contract": CACHE_WORKLOAD_CONTRACT}
        if method == "GET":
            return [
                {
                    "id": identifier,
                    "workflow": state["profiles"][0]["binding"]["workflow_id"],
                    "environment": "staging",
                    "state": "COMPLETED",
                    "summary": {"cache_trial_eligible": True},
                }
            ]
        if mutate:
            mutate()
        result = assessment(
            project_id=path.split("/")[3],
            environment="staging",
            workflow=state["profiles"][0]["binding"]["workflow_id"],
            analysis_id=identifier,
            source_binding_sha256=fingerprint(body["source_binding"]),
            cache_execution_sha256=fingerprint(body["cache_execution"]),
            baseline_model=body["baseline_model"],
        )
        if change:
            result = change(result)
        return result

    return control, calls


def test_agent_assessment_never_queues_work_and_preserves_same_root_binding(tmp_path):
    root = committed_application(tmp_path / "application")
    state = analyze(root)
    control, calls = control_for(state)
    result = assess(root, control, uuid4(), cache_execution=contract())
    assert result["decision"] == "prepare_separate_cache_trial"
    assert len(calls) == 3 and calls[-1][1].endswith("/cache-trial")
    assert load_session(root)["phase"] == state["phase"]
    assert load_session(root)["workload_assessments"][-1] == result
    assert result["provider_calls"] == result["encrypted_storage_reads"] == 0


@pytest.mark.parametrize(
    "kind", ["hash", "source", "expired", "authority", "project", "environment", "workflow"]
)
def test_agent_refuses_invalid_scope_authority_and_expired_evidence_without_journaling(
    tmp_path, kind
):
    root = committed_application(tmp_path / "application")
    state = analyze(root)

    def changed(result):
        if kind == "hash":
            result["assessment_sha256"] = "0" * 64
            return result
        if kind == "source":
            result["source_binding_sha256"] = "0" * 64
        elif kind == "expired":
            result["valid_until"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        elif kind == "project":
            result["project_id"] = str(uuid4())
        elif kind == "environment":
            result["environment"] = "production"
        elif kind == "workflow":
            result["workflow"] = "other-workflow"
        else:
            result["paid_calls_authorized"] = True
        result["assessment_sha256"] = fingerprint(
            {key: value for key, value in result.items() if key != "assessment_sha256"}
        )
        return result

    control, _ = control_for(state, change=changed)
    with pytest.raises(ValueError):
        assess(root, control, uuid4(), cache_execution=contract(), analysis_id=uuid4())
    assert "workload_assessments" not in load_session(root)
    assert load_session(root)["revision"] == state["revision"]


def test_source_change_during_metadata_request_cannot_commit_stale_assessment(tmp_path):
    root = committed_application(tmp_path / "application")
    state = analyze(root)
    path = root / "app.py"
    control, _ = control_for(
        state, mutate=lambda: path.write_bytes(path.read_bytes() + b"\n# changed\n")
    )
    with pytest.raises(ValueError):
        assess(root, control, uuid4(), cache_execution=contract())
    assert "workload_assessments" not in load_session(root)


def test_missing_collector_contract_and_empty_retained_page_never_create_a_job(tmp_path):
    root = committed_application(tmp_path / "application")
    analyze(root)
    calls = []

    def old(method, path, body=None):
        calls.append(method)
        return {}

    with pytest.raises(ValueError, match="UPGRADE_REQUIRED"):
        assess(root, old, uuid4(), cache_execution=contract())
    assert calls == ["GET"]

    def empty(method, path, body=None):
        calls.append(method)
        return (
            {"cache_workload_assessment_contract": CACHE_WORKLOAD_CONTRACT}
            if path.endswith("/ready")
            else []
        )

    result = assess(root, empty, uuid4(), cache_execution=contract())
    assert result["decision"] == "abstain"
    assert result["reasons"] == ["NO_RETAINED_ELIGIBLE_REQUEST_PROFILE_ANALYSIS"]
    assert set(calls) == {"GET"}


def test_source_workload_export_is_metadata_only_and_requires_clean_source(tmp_path):
    from cutback.agent_cache_workload import source_input

    root = committed_application(tmp_path / "application")
    state = analyze(root)
    body = source_input(root, cache_execution=contract())
    assert set(body) == {"source_binding", "baseline_model", "cache_execution"}
    assert body["source_binding"] == state["profiles"][0]["binding"]
    assert "payload" not in body and "instruction" not in body
    original = load_session(root)
    path = root / "app.py"
    path.write_bytes(path.read_bytes() + b"\n# changed\n")
    with pytest.raises(ValueError):
        source_input(root, cache_execution=contract())
    assert load_session(root) == original


def test_cli_exports_current_binding_without_network_or_request_template(tmp_path, monkeypatch):
    import json
    import socket

    from typer.testing import CliRunner

    from cutback.cli import app

    root = committed_application(tmp_path / "application")
    state = analyze(root)
    configuration, output = tmp_path / "cache.json", tmp_path / "workload.json"
    original = load_session(root)
    configuration.write_text(json.dumps(contract()), encoding="utf-8")
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("OFFLINE_ONLY"))
    result = CliRunner().invoke(
        app,
        [
            "workflow",
            "workload-input",
            "--target",
            str(root),
            "--exact-cache-contract",
            str(configuration),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.stdout
    body = json.loads(output.read_text(encoding="utf-8"))
    assert body["source_binding"] == state["profiles"][0]["binding"]
    assert set(body) == {"source_binding", "baseline_model", "cache_execution"}
    assert "instruction" not in body and "request" not in body
    assert load_session(root) == original
