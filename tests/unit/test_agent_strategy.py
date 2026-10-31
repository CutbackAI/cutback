"""Automatic source adapter selection never grants rights, money or quality evidence."""

import json
import re

import pytest
from support_agent import fresh_application

from cutback.agent import analyze, load_session, prepare
from cutback.agent_strategy import freeze_preparation, preparation_options, select_preparation
from cutback.core.workspace import workspace_lock, write_private


@pytest.mark.parametrize("minimum", [0, 10001, True, 1.5, "6"])
def test_invalid_evidence_minimum_is_refused_before_any_collector_write(tmp_path, minimum):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("COLLECTOR_MUST_NOT_BE_CALLED")

    with pytest.raises(ValueError, match="EVIDENCE_SAMPLE_MINIMUM_ONE_TO_TEN_THOUSAND_REQUIRED"):
        prepare(
            tmp_path,
            forbidden,
            "unused",
            files=["cases.jsonl"],
            rights_basis="synthetic",
            rights_ref="owned",
            max_usd="1",
            min_test_samples=minimum,
        )
    assert not calls


@pytest.mark.parametrize("force_color", [False, True])
def test_cli_exposes_automatic_preparation_without_connecting(monkeypatch, force_color):
    from typer.testing import CliRunner

    from cutback.cli import app

    if force_color:
        monkeypatch.setenv("FORCE_COLOR", "1")
    else:
        monkeypatch.delenv("FORCE_COLOR", raising=False)
    response = CliRunner().invoke(app, ["workflow", "prepare", "--help"], color=False)
    visible = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", response.stdout)
    assert response.exit_code == 0 and "--automatic" in visible


def test_wrapper_contracts_take_precedence_and_do_not_claim_economic_superiority():
    state = {
        "analysis_sha256": "a" * 64,
        "profiles": [{"binding": {"workflow_id": "owned"}}],
        "context_producers": [{"workflow_id": "owned"}],
    }
    choice = select_preparation(state)
    assert choice["mode"] == "joint_model_context"
    assert choice["provider_calls"] == 0 and choice["expected_savings_usd"] is None
    assert choice["other_engines"]["exact_cache"].startswith("requires_repeat")
    assert preparation_options(state)[0]["modes"] == ["joint_model_context"]
    state["routing_producers"] = [{"workflow_id": "owned"}]
    with pytest.raises(ValueError, match="UNAMBIGUOUS_SOURCE_ADAPTER"):
        select_preparation(state)


def test_packaged_http_entry_has_one_profile_despite_two_scanner_hints(tmp_path):
    from cutback.agent import selected_profile
    from cutback.core.inventory import workflow_identity
    from cutback.scanner import scan

    package = tmp_path / "src/owned_app"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    fresh_application(package)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="owned-app"\nversion="0.0.1"\n', encoding="utf-8"
    )
    (package / "web.py").write_text(
        "from fastapi import FastAPI\nfrom .app import classify\napp = FastAPI()\n"
        '@app.post("/route")\ndef route(text):\n    return classify(text)\n',
        encoding="utf-8",
    )
    inventory = scan(tmp_path)
    entries = inventory["call_sites"][0]["entry_points"]
    kinds = {entry["kind"] for entry in entries}
    assert {"http_route", "function_root"} <= kinds
    identities = {workflow_identity(entry["file"], entry["symbol"])[0] for entry in entries}
    assert len(identities) == 1
    workflow = identities.pop()
    state = analyze(tmp_path, workflow=workflow)
    assert len(state["profiles"]) == 1
    assert selected_profile(tmp_path, state, workflow).binding.workflow_id == workflow
    assert select_preparation(state, workflow)["mode"] == "native_search"


def test_workflow_ambiguity_and_unsupported_source_never_pick_an_arbitrary_engine():
    state = {
        "analysis_sha256": "a" * 64,
        "profiles": [{"binding": {"workflow_id": "first"}}, {"binding": {"workflow_id": "second"}}],
    }
    with pytest.raises(ValueError, match="ONE_QUALIFIED_WORKFLOW"):
        select_preparation(state)
    assert select_preparation(state, "second")["workflow_id"] == "second"
    with pytest.raises(ValueError, match="ONE_QUALIFIED_WORKFLOW"):
        select_preparation(state, "absent")


def test_automatic_intent_survives_interruption_is_private_and_cannot_be_reinterpreted(
    tmp_path, monkeypatch
):
    import socket

    fresh_application(tmp_path)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("OFFLINE_ONLY"))
    state = analyze(tmp_path)
    assert state["preparation_options"][0]["modes"] == ["native_search"]
    selected = freeze_preparation(tmp_path)
    frozen = load_session(tmp_path)
    assert frozen["automatic_preparation"] == selected
    assert "Owned synthetic classification task" not in json.dumps(frozen)
    # A restart before the first collector call resumes the same nonbillable intent.
    assert freeze_preparation(tmp_path) == selected
    assert load_session(tmp_path)["revision"] == frozen["revision"]
    with workspace_lock(tmp_path / ".cutback/agent"):
        frozen["automatic_preparation"]["mode"] = "source_routing"
        write_private(tmp_path / ".cutback/agent/session.json", frozen)
    with pytest.raises(ValueError, match="INTENT_CHANGED_REANALYZE"):
        freeze_preparation(tmp_path)


@pytest.mark.parametrize(
    "options",
    [
        {"search": True},
        {"joint_context": True},
        {"learned_routing": True},
        {"for_campaign": True},
        {"criteria_generator_provider": "openai"},
    ],
)
def test_automatic_mode_refuses_conflicting_authority_before_collector_io(tmp_path, options):
    fresh_application(tmp_path)
    analyze(tmp_path)
    with pytest.raises(ValueError, match="CANNOT_OVERRIDE_EXPLICIT_ENGINE"):
        prepare(
            tmp_path,
            None,
            "unused",
            files=[],
            rights_basis="synthetic",
            rights_ref="owned",
            max_usd="0",
            automatic=True,
            **options,
        )
    assert "automatic_preparation" not in load_session(tmp_path)
