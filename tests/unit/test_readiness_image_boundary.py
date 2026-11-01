"""Real Docker cleanup after a verified running client is lost, with ownership protection."""

import importlib.util
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest
from support_application_container import container_image as container_image


def module(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "scripts/application_readiness_acceptance.py"
    spec = importlib.util.spec_from_file_location("owned_readiness_image_boundary", path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def docker(*arguments):
    result = subprocess.run(["docker", *arguments], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, "OWNED_READINESS_DOCKER_OPERATION_FAILED"
    return result.stdout.strip()


def test_actual_offline_installed_diagnosis_and_confirmed_container_cleanup(
    monkeypatch, container_image
):
    loaded = module(monkeypatch)
    receipt = loaded.qualify(container_image.image_id, checked=loaded.checked)
    assert receipt["container_cleanup_confirmed"] and receipt["whole_policy_wrapper_preserved"]
    assert receipt["application_commands_executed"] == receipt["paid_provider_calls"] == 0


def test_lost_running_docker_client_still_cleans_its_own_container(monkeypatch, container_image):
    loaded, owner = module(monkeypatch), uuid4()
    name = "cutback-readiness-" + owner.hex
    monkeypatch.setattr(loaded, "uuid4", lambda: owner)
    monkeypatch.setattr(loaded, "FIXTURE", "import time;time.sleep(120)")
    observed_running = []

    def lose_client(arguments, *, source, timeout):
        process = subprocess.Popen(
            arguments,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            process.stdin.write(source)
            process.stdin.close()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                inspected = subprocess.run(
                    ["docker", "container", "inspect", "--format", "{{.State.Running}}", name],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if inspected.returncode == 0 and inspected.stdout.strip() == "true":
                    observed_running.append(True)
                    break
                time.sleep(0.05)
            assert observed_running, "OWNED_READINESS_CONTAINER_DID_NOT_START"
        finally:
            process.kill()
            process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()
        raise subprocess.TimeoutExpired("owned-readiness-client", 1)

    with pytest.raises(subprocess.TimeoutExpired):
        loaded.qualify(container_image.image_id, checked=lose_client)
    assert (
        observed_running
        and name not in docker("container", "ls", "--all", "--format", "{{.Names}}").splitlines()
    )


def test_name_collision_never_removes_a_container_with_different_ownership(
    monkeypatch, container_image
):
    loaded, owner = module(monkeypatch), uuid4()
    name = "cutback-readiness-" + owner.hex
    monkeypatch.setattr(loaded, "uuid4", lambda: owner)
    # This stopped container belongs to this test, not the invocation under test.
    identifier = docker(
        "create",
        "--pull",
        "never",
        "--name",
        name,
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--label",
        "com.cutback.readiness.owner=other-owned-fixture",
        "--entrypoint",
        "/app/.venv/bin/python",
        container_image.image_id,
        "-c",
        "pass",
    )
    try:
        with pytest.raises(ValueError, match="READINESS_CONTAINER_OWNERSHIP_CONFLICT"):
            loaded.qualify(container_image.image_id, checked=loaded.checked)
        assert docker("container", "inspect", "--format", "{{.Id}}", name) == identifier
    finally:
        docker("container", "rm", "--force", identifier)
