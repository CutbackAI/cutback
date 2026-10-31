"""Attribution refresh is parse-only and binds the clean current commit."""

import json
import socket

import pytest
from integration.test_agent_cache_workload import committed_application

from cutback.agent_source_map import refresh
from cutback.core.workspace import read_private


def test_refresh_does_not_import_application_or_contact_a_service(tmp_path, monkeypatch):
    from openai import OpenAI

    root = committed_application(tmp_path / "application")
    original = (root / "app.py").read_bytes()
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("OFFLINE_ONLY"))
    monkeypatch.setattr(OpenAI, "__init__", lambda *a, **k: pytest.fail("NO_APPLICATION_IMPORT"))
    result = refresh(root)
    assert result["provider_calls"] == 0 and result["application_source_changed"] is False
    assert result["runtime_restart_required"] is True and result["mapped_files"] == 1
    mapping = read_private(root / result["source_map_path"])
    assert mapping["source_commit"] == result["source_commit"]
    assert mapping["files"][0]["path"] == "app.py"
    assert "input" not in json.dumps(mapping)
    assert (root / "app.py").read_bytes() == original


def test_dirty_source_does_not_replace_existing_map(tmp_path):
    root = committed_application(tmp_path / "application")
    result = refresh(root)
    path = root / result["source_map_path"]
    original = path.read_bytes()
    application = root / "app.py"
    application.write_bytes(application.read_bytes() + b"\n# changed\n")
    with pytest.raises(ValueError, match="CLEAN_TRACKED_WORKSPACE"):
        refresh(root)
    assert path.read_bytes() == original


def test_source_change_while_scanning_cannot_publish_map(tmp_path, monkeypatch):
    import cutback.scanner

    root = committed_application(tmp_path / "application")
    original = cutback.scanner.scan

    def change(target):
        inventory = original(target)
        path = root / "app.py"
        path.write_bytes(path.read_bytes() + b"\n# changed\n")
        return inventory

    monkeypatch.setattr(cutback.scanner, "scan", change)
    with pytest.raises(ValueError):
        refresh(root)
    assert not (root / ".cutback/source-map.json").exists()
