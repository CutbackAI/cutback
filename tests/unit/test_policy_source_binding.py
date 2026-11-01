"""Complete source qualification, exact tracked bytes and parse-only refusal."""

import builtins

import pytest
from test_application_workspaces import repository
from test_source_improvement_policy import policy

import cutback.policy_source as source
from cutback.application_checks import git
from cutback.core.policy_producer import PROGRAM, PROGRAM_SHA256, PolicySourceBinding, qualify


def application(tmp_path):
    root, _ = repository(tmp_path)
    (root / "app.py").write_text(PROGRAM, encoding="utf-8")
    (root / "policy.json").write_text(policy(routed=True).model_dump_json(), encoding="utf-8")
    git(root, "add", "app.py", "policy.json")
    git(root, "commit", "-m", "Independent common-policy source fixture")
    return root


def test_exact_metadata_binding_without_importing_provider_or_application(tmp_path, monkeypatch):
    root = application(tmp_path)
    original = builtins.__import__

    def no_application_or_provider(name, *args, **kwargs):
        if name.split(".")[0] in {"openai", "anthropic", "fastapi", "app"}:
            raise AssertionError("parse-only inspection imported application/provider code")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_application_or_provider)
    selected, binding = source.inspect_configuration(root, "app.py")
    assert binding.program_sha256 == PROGRAM_SHA256
    assert binding.policy_sha256 == selected.policy_sha256
    assert binding.workflow == selected.workflow
    assert "OWNED_PRIVATE_VARIANT" not in binding.model_dump_json()
    assert "OWNED_PRIVATE_BASE_INSTRUCTION" not in binding.model_dump_json()


def test_mutation_after_snapshot_cannot_bind_different_policy_bytes(tmp_path, monkeypatch):
    root = application(tmp_path)
    snapshot, calls = source.snapshot, []

    def changed(actual):
        frozen = snapshot(actual)
        if not calls:
            calls.append(True)
            current = policy().model_dump(mode="json")
            current["strategy"]["instruction"] = "Concurrent private source change"
            from cutback.core.improvement_policy import SourceImprovementPolicy

            (root / "policy.json").write_text(
                SourceImprovementPolicy.model_validate(current).model_dump_json(), encoding="utf-8"
            )
        return frozen

    monkeypatch.setattr(source, "snapshot", changed)
    with pytest.raises(ValueError, match="CHANGED_DURING_INSPECTION"):
        source.inspect_configuration(root, "app.py")


def test_program_changes_cannot_execute_side_effects_and_paths_remain_paired(tmp_path):
    root = application(tmp_path)
    malicious = PROGRAM + "\nPath('OWNED_MUST_NOT_EXECUTE').write_text('forbidden')\n"
    with pytest.raises(ValueError, match="UNQUALIFIED_NATIVE_PROGRAM"):
        qualify(malicious.encode())
    assert not (root / "OWNED_MUST_NOT_EXECUTE").exists()
    assert qualify(('"""An ordinary module description."""\n' + PROGRAM).encode()) == PROGRAM_SHA256
    _, binding = source.inspect_configuration(root, "app.py")
    invalid = binding.model_dump(mode="json")
    invalid["configuration_file"] = "other-policy.json"
    with pytest.raises(ValueError, match="EXACT_PAIRED_PROGRAM"):
        PolicySourceBinding.model_validate(invalid)


def test_oversized_producer_is_refused_without_parsing():
    with pytest.raises(ValueError, match="PRODUCER_BYTE_BOUND"):
        qualify(b" " * 65537)
