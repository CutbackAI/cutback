import json

import pytest
from support_agent import fresh_application
from typer.testing import CliRunner

from cutback.agent import analyze, load_session
from cutback.cli import app
from cutback.core.workspace import workspace_lock
from cutback.evals.records import read_records


def test_offline_agent_discovers_root_fixtures_and_resumes_without_payloads(tmp_path, monkeypatch):
    fresh_application(tmp_path)
    (tmp_path / "app.py").write_text(
        (tmp_path / "app.py").read_text() + '\nraise RuntimeError("DO_NOT_EXECUTE")\n'
    )
    import socket

    def deny(*args, **kwargs):
        raise AssertionError("NETWORK_FORBIDDEN")

    monkeypatch.setattr(socket, "create_connection", deny)
    state = analyze(tmp_path)
    assert len(state["profiles"]) == 1 and state["fixtures"][0]["path"] == "cases.jsonl"
    assert state["next_action"]["code"] == "REVIEW_DATA_RIGHTS_AND_PREPARE"
    assert "Owned synthetic classification task" not in json.dumps(state)
    assert "Owned synthetic AA topic" not in json.dumps(state)
    again = analyze(tmp_path)
    assert again["id"] == state["id"] and again["revision"] == state["revision"]
    cli = CliRunner().invoke(app, ["workflow", "status", "--target", str(tmp_path)])
    assert cli.exit_code == 0 and json.loads(cli.stdout)["phase"] == "ANALYZED"
    with (
        workspace_lock(tmp_path / ".cutback/agent"),
        pytest.raises(ValueError, match="WORKFLOW_BUSY"),
    ):
        analyze(tmp_path)
    (tmp_path / "app.py").write_text((tmp_path / "app.py").read_text() + "# modified\n")
    changed = analyze(tmp_path)
    assert changed["phase"] == "SOURCE_CHANGED" and "plan" not in changed
    assert load_session(tmp_path)["analysis_sha256"] != state["analysis_sha256"]


def test_permitted_nested_mapping_preserves_unknown_gold_and_discovery_excludes_secrets(tmp_path):
    fresh_application(tmp_path)
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets/cases.jsonl").write_text("sensitive")
    path = tmp_path / "nested.ndjson"
    path.write_text(
        json.dumps({"request": {"text": "owned text"}, "result": {"reference": "old answer"}})
    )
    rows = read_records(path, mapping={"input": "request.text", "reference": "result.reference"})
    assert rows[0].input == "owned text" and rows[0].gold is None
    assert "secrets/cases.jsonl" not in [f["path"] for f in analyze(tmp_path)["fixtures"]]
    with pytest.raises(ValueError, match="UNSAFE_RECORD_MAPPING"):
        read_records(path, mapping={"input": "request.__dict__"})


def test_agent_workspace_refuses_symlink(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (tmp_path / ".cutback").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    with pytest.raises(ValueError, match="UNSAFE_WORKSPACE_DIRECTORY"):
        analyze(tmp_path)
    assert not list(outside.iterdir())


def test_new_session_archives_old_plan_but_never_discards_active_authority(tmp_path):
    from uuid import uuid4

    from cutback.core.workspace import write_private

    fresh_application(tmp_path)
    original = analyze(tmp_path)
    state = load_session(tmp_path)
    state.update(phase="PLAN_READY", plan={"id": str(uuid4()), "plan_hash": "a" * 64})
    write_private(tmp_path / ".cutback/agent/session.json", state)
    fresh = analyze(tmp_path, new_session=True)
    assert fresh["id"] != original["id"] and fresh["previous_session_id"] == original["id"]
    assert "plan" not in fresh and fresh["phase"] == "ANALYZED"
    assert (tmp_path / f".cutback/agent/session-{original['id']}.json").exists()
    active = load_session(tmp_path)
    active.update(phase="QUEUED", run_id=str(uuid4()))
    write_private(tmp_path / ".cutback/agent/session.json", active)
    with pytest.raises(ValueError, match="ACTIVE_WORKFLOW_MUST_BE_RECONCILED"):
        analyze(tmp_path, new_session=True)
    path = tmp_path / "app.py"
    path.write_text(path.read_text() + "# changed while running\n")
    changed = analyze(tmp_path)
    assert changed["phase"] == "QUEUED" and changed["run_id"] == active["run_id"]
    assert changed["source_status"] == "CHANGED_RESCAN_AFTER_RUN"


def test_campaign_basis_prefers_pure_downgrade_and_preserves_request_contract():
    from cutback.agent import campaign_basis_candidates
    from cutback.evals.experiments import ModelSpec

    baseline = ModelSpec(
        key="baseline",
        provider="openai",
        model="gpt-4.1",
        instruction="Keep the independent task contract.",
        max_output_tokens=64,
    )
    (choice,) = campaign_basis_candidates(baseline)
    assert choice.model == "gpt-4.1-mini" and choice.max_output_tokens == 64
    assert choice.instruction == baseline.instruction
    smallest = baseline.model_copy(update={"model": "gpt-4.1-mini", "max_output_tokens": 16})
    (control,) = campaign_basis_candidates(smallest)
    assert control.key != smallest.key
    assert control.model_dump(exclude={"key"}) == smallest.model_dump(exclude={"key"})
    unknown = baseline.model_copy(update={"model": "synthetic-unpriced"})
    with pytest.raises(ValueError, match="BASELINE_PRICE_UNKNOWN"):
        campaign_basis_candidates(unknown)
