"""Installed-package local service lifecycle; only owned Docker resources are touched."""

import os

import pytest


@pytest.mark.skipif(
    os.getenv("CUTBACK_TEST_LOCAL_STACK") != "true", reason="explicit owned Docker lifecycle gate"
)
def test_persistent_local_stack_default_privacy_restart_and_ownership(tmp_path):
    from cutback.agent_control import Control, connect
    from cutback.core.workspace import write_private
    from cutback.local_stack import configured, docker, down, free_port, owned, status, up

    port = free_port()
    config = None
    try:
        first = up(tmp_path, port=port)
        assert first["status"] == "READY" and first["payload_storage_enabled"] is False
        location, config = configured(tmp_path)
        control = Control(config["endpoint"], config["admin"])
        project = control("POST", "/v1/projects", {"slug": "owned-persistence-fixture"})
        assert down(tmp_path)["database"] == "preserved"
        assert up(tmp_path, port=port, allow_payloads=True)["status"] == "READY"
        _, restarted = configured(tmp_path)
        assert config["payload_key"] == restarted["payload_key"]
        assert any(row["id"] == project["id"] for row in control("GET", "/v1/projects"))
        scoped, scoped_project = connect(tmp_path, local=True)
        assert scoped("GET", "/v1/projects/" + scoped_project)["id"] == scoped_project
        repeated, same_project = connect(tmp_path)
        assert same_project == scoped_project
        assert repeated("GET", "/v1/projects/" + same_project)["id"] == scoped_project
        config = restarted
        tampered = {**config, "owner": "wrong-owner"}
        write_private(location / "config.json", tampered)
        with pytest.raises(ValueError, match="OWNERSHIP_MISMATCH"):
            down(tmp_path)
        write_private(location / "config.json", config)
        assert status(tmp_path)["status"] == "READY"
        assert "admin" not in status(tmp_path) and "payload_key" not in status(tmp_path)
    finally:
        if config:
            location, _ = configured(tmp_path)
            write_private(location / "config.json", config)
            down(tmp_path)
            owned(config)
            docker("rm", config["container"])
            # Exact labelled test-owned volume; normal collector down never deletes data.
            import json

            volume = json.loads(docker("volume", "inspect", config["volume"]))[0]
            assert volume["Labels"]["org.cutback.local.owner"] == config["owner"]
            docker("volume", "rm", config["volume"])
