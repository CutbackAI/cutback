"""Policy-native attribution is parse-only and cannot invalidate its fixed producer."""

import builtins
import json
from pathlib import Path

import pytest
from support_policy_application import application

from cutback.agent import analyze
from cutback.application_checks import git
from cutback.core.instrumentation_plan import instrument_plan, refresh_source_map
from cutback.core.policy_producer import ASYNC_PROGRAM, PROGRAM
from cutback.scanner import scan


@pytest.mark.parametrize("asynchronous", [False, True])
def test_scanner_maps_policy_calls_without_reading_config_or_optional_runtime(
    tmp_path, monkeypatch, asynchronous
):
    (tmp_path / "app.py").write_text(ASYNC_PROGRAM if asynchronous else PROGRAM, encoding="utf-8")
    (tmp_path / "policy.json").write_text(
        "OWNED_PRIVATE_CONFIG_MUST_NOT_BE_PARSED", encoding="utf-8"
    )
    original = builtins.__import__

    def offline(name, *args, **kwargs):
        if name.startswith(
            ("openai", "anthropic", "cutback.evals", "cutback.server", "cutback.source_policy")
        ):
            raise AssertionError("Offline scanner imported optional runtime")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", offline)
    result = scan(tmp_path)
    (site,) = result["call_sites"]
    assert site["provider"] == "openai" and site["sdk_surface"] == "responses.create"
    assert site["requested_model"] is None and site["configured_max_output_tokens"] is None
    assert site["actual_cost"] is site["actual_tokens"] is site["actual_latency"] is None
    assert "source_policy_configuration_unread" in site["unresolved"]
    assert "cutback_policy_native_wrapper" in site["evidence"]
    assert "OWNED_PRIVATE_CONFIG" not in json.dumps(result)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_instrumentation_preserves_fixed_producer_and_finds_external_process_startup(
    tmp_path, asynchronous
):
    root = application(tmp_path, async_consumer=asynchronous)
    before = (root / "app.py").read_bytes()
    with pytest.raises(ValueError, match="SOURCE_POLICY_EXTERNAL_STARTUP_REQUIRED"):
        instrument_plan(root, entrypoint=Path("app.py"))
    (root / "startup.py").write_text(
        "from app import classify\n"
        + ("async " if asynchronous else "")
        + "def start(client, text):\n    return "
        + ("await " if asynchronous else "")
        + "classify(client, text)\n",
        encoding="utf-8",
    )
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Synthetic",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "Independent external startup",
    )
    result = analyze(root, workflow="routing-task")
    candidates = {row["source_file"] for row in result["instrumentation"]["proposals"]}
    assert "startup.py" in candidates and "app.py" not in candidates
    proposed = instrument_plan(root, entrypoint=Path("startup.py"))
    assert proposed["source_map"]["files"] and "cutback:bootstrap" in proposed["after"]
    assert (root / "app.py").read_bytes() == before
    assert (root / "startup.py").read_text("utf-8") != proposed["after"]


def test_source_map_refresh_keeps_consumer_bytes_and_refuses_dirty_revision(tmp_path):
    root = application(tmp_path)
    producer = (root / "app.py").read_bytes()
    result = refresh_source_map(root)
    mapping = json.loads((root / ".cutback/source-map.json").read_text("utf-8"))
    assert result["source_commit"] == mapping["source_commit"]
    assert result["mapped_files"] == len(mapping["files"]) and mapping["files"]
    assert result["provider_calls"] == 0 and result["application_source_changed"] is False
    assert (root / "app.py").read_bytes() == producer
    before = (root / ".cutback/source-map.json").read_bytes()
    (root / "app.py").write_bytes(producer + b"\n# changed\n")
    with pytest.raises(ValueError):
        refresh_source_map(root)
    assert (root / ".cutback/source-map.json").read_bytes() == before
