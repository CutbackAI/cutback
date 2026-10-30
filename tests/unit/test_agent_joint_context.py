"""Offline discovery qualifies the actual wrapper and abstains on unverified code."""

import json

import pytest
from support_compression_application import application
from support_retrieval_application import git

from cutback.agent import analyze, next_action
from cutback.agent_context import selected_source


def test_offline_agent_finds_qualified_joint_context_source_without_importing_or_calls(
    tmp_path, monkeypatch
):
    import socket

    root = application(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("OFFLINE_DISCOVERY_CANNOT_CONTACT_NETWORK")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    state = analyze(root)
    assert len(state["context_producers"]) == 1
    assert state["context_producers"][0]["producer_binding"]["file"] == "app.py"
    assert "deadline-days" not in json.dumps(state)
    assert state["provider_calls"] == 0
    assert state["next_action"]["code"] == "REVIEW_DATA_RIGHTS_AND_PREPARE_JOINT_CONTEXT"
    resumed = analyze(root)
    assert resumed["id"] == state["id"] and resumed["revision"] == state["revision"]
    path = root / "app.py"
    path.write_bytes(path.read_bytes() + b'\nraise RuntimeError("NEVER_IMPORT")\n')
    git(root, "add", ".")
    git(root, "commit", "-m", "Owned unsupported code")
    with pytest.raises(ValueError, match="NOT_QUALIFIED"):
        selected_source(root, state)
    assert not analyze(root)["context_producers"]


def test_completed_joint_agent_requires_separate_confirmation_and_exact_approval():
    action = next_action({"phase": "COMPLETED", "search_mode": "joint_model_context"})
    assert action["code"] == "SELECT_CANDIDATE_AND_FREEZE_CONFIRMATION"
    assert action["new_exact_approval_required"] and action["provider_calls"] == 0
