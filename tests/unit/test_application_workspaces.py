"""Actual independent repositories/checks; no hooks, ignored data or ancestors exported."""

import os
from pathlib import Path

import pytest

from cutback.application_checks import ApplicationCommand, git, prepare, run_checks, snapshot
from cutback.application_workspaces import materialize


def repository(tmp_path, *, algorithm="sha1"):
    root = tmp_path / "original"
    root.mkdir()
    git(root, "init", "--template=", "--object-format=" + algorithm)
    git(root, "config", "user.name", "Independent Synthetic Owner")
    git(root, "config", "user.email", "synthetic@example.invalid")
    (root / ".gitignore").write_text(".env\n.private/\n__pycache__/\n", encoding="utf-8")
    (root / ".gitattributes").write_text("*.json filter=owned-test\n", encoding="utf-8")
    (root / "settings.json").write_bytes(b'{"model":"older"}\n')
    (root / "check_application.py").write_text(
        "import json,os\nfrom pathlib import Path\n"
        "assert not any(key in os.environ for key in ('OPENAI_API_KEY','CUTBACK_CONTROL_KEY'))\n"
        "assert json.loads(Path('settings.json').read_text())['model'] in ('baseline','cheap')\n",
        encoding="utf-8",
    )
    (root / "nested").mkdir()
    (root / "nested/π.txt").write_text("independent UTF-8 source\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "Independent original ancestor")
    ancestor = git(root, "rev-parse", "HEAD").decode().strip()
    (root / "settings.json").write_bytes(b'{"model":"baseline"}\n')
    git(root, "add", "settings.json")
    git(root, "commit", "-m", "Independent source base")
    (root / ".env").write_text("owned-ignored-never-exported", encoding="utf-8")
    return root, ancestor


@pytest.mark.parametrize("algorithm", ["sha1", "sha256"])
def test_exact_base_one_parent_proposal_and_actual_both_tree_checks(
    tmp_path, monkeypatch, algorithm
):
    root, ancestor = repository(tmp_path, algorithm=algorithm)
    frozen = snapshot(root)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-ambient-must-not-reach-check")
    monkeypatch.setenv("CUTBACK_CONTROL_KEY", "synthetic-ambient-must-not-reach-check")
    result = materialize(
        root, tmp_path / "review", frozen, {"settings.json": b'{"model":"cheap"}\n'}
    )
    base, proposed = Path(result["base_root"]), Path(result["proposed_root"])
    assert result["status"] == "WORKSPACES_READY"
    assert (
        result["ancestor_history_copied"] is False
        and result["application_checks_executed"] is False
    )
    assert snapshot(root) == snapshot(base) == frozen
    for exported in (base, proposed):
        assert not (exported / ".env").exists()
        assert not (exported / ".git/objects" / ancestor[:2] / ancestor[2:]).exists()
        assert (exported / ".git/shallow").read_text().strip() == frozen.commit
        assert b"filter.owned-test" not in (exported / ".git/config").read_bytes()
    raw_commit = git(proposed, "cat-file", "commit", result["proposed_commit"])
    candidate_blob = git(proposed, "rev-parse", "HEAD:settings.json").decode().strip()
    assert not (base / ".git/objects" / candidate_blob[:2] / candidate_blob[2:]).exists()
    assert raw_commit.count(b"\nparent ") == 1
    assert b"\nparent " + frozen.commit.encode() + b"\n" in raw_commit
    assert b"synthetic@example.invalid" not in raw_commit
    contract = prepare(
        base, proposed, [ApplicationCommand(key="consumer", arguments=["check_application.py"])]
    )
    receipt = run_checks(contract, base, proposed, execute_local=True)
    assert receipt["all_passed"] is True and len(receipt["results"]) == 2
    assert snapshot(root) == frozen


def test_source_filters_and_hooks_are_never_executed_or_copied(tmp_path):
    root, _ = repository(tmp_path)
    sentinel = tmp_path / "unexpected-hook-or-filter"
    hook = root / ".git/hooks"
    hook.mkdir()
    for name in ("post-checkout", "post-index-change", "pre-commit"):
        path = hook / name
        path.write_text("#!/bin/sh\ntouch '" + sentinel.as_posix() + "'\n", encoding="utf-8")
        path.chmod(0o700)
    git(root, "config", "filter.owned-test.required", "true")
    git(root, "config", "filter.owned-test.clean", "exit 76")
    git(root, "config", "filter.owned-test.smudge", "exit 77")
    frozen = snapshot(root)
    result = materialize(
        root, tmp_path / "review", frozen, {"settings.json": b'{"model":"cheap"}\n'}
    )
    assert not sentinel.exists()
    for name in ("base_root", "proposed_root"):
        assert not (Path(result[name]) / ".git/hooks").exists()
    assert snapshot(root) == frozen


def test_preserves_raw_crlf_and_executable_mode(tmp_path):
    root, _ = repository(tmp_path)
    path = root / "check_application.py"
    if os.name != "nt":
        path.chmod(0o755)
        git(root, "add", "check_application.py")
        git(root, "commit", "-m", "Independent executable mode")
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    frozen = snapshot(root)
    result = materialize(
        root, tmp_path / "review", frozen, {"settings.json": b'{"model":"cheap"}\n'}
    )
    assert snapshot(Path(result["base_root"])) == frozen
    assert (Path(result["base_root"]) / "check_application.py").read_bytes() == path.read_bytes()


@pytest.mark.parametrize("problem", ["existing", "inside", "dirty", "empty", "untracked"])
def test_refuses_unsafe_or_stale_preparation_without_changing_source(tmp_path, problem):
    root, _ = repository(tmp_path)
    frozen = snapshot(root)
    destination = tmp_path / "review"
    changes = {"settings.json": b'{"model":"cheap"}\n'}
    if problem == "existing":
        destination.mkdir()
        (destination / "preserve.txt").write_text("preserve", encoding="utf-8")
    elif problem == "inside":
        destination = root / "nested-review"
    elif problem == "dirty":
        (root / "settings.json").write_bytes(b'{"model":"mutated"}\n')
    elif problem == "empty":
        changes["settings.json"] = (root / "settings.json").read_bytes()
    else:
        changes = {"outside.json": b"{}"}
    with pytest.raises((ValueError, FileExistsError)):
        materialize(root, destination, frozen, changes)
    assert (root / ".env").read_text() == "owned-ignored-never-exported"
    if problem == "existing":
        assert (destination / "preserve.txt").read_text() == "preserve"
    if problem != "dirty":
        assert snapshot(root) == frozen


@pytest.mark.parametrize("location", ["source", "destination_parent"])
def test_symbolic_directory_redirects_are_refused_before_export(tmp_path, location):
    root, _ = repository(tmp_path)
    frozen = snapshot(root)
    link = tmp_path / "redirect"
    target = root if location == "source" else tmp_path / "owned-output"
    target.mkdir(exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Host cannot create the required directory-symlink fixture")
    source = link if location == "source" else root
    destination = tmp_path / "review" if location == "source" else link / "review"
    with pytest.raises(ValueError):
        materialize(source, destination, frozen, {"settings.json": b'{"model":"cheap"}\n'})
    assert snapshot(root) == frozen and not destination.exists()


def test_source_mutation_during_export_leaves_failed_metadata_and_no_review(tmp_path, monkeypatch):
    import cutback.application_workspaces as module
    from cutback.core.workspace import read_private

    root, _ = repository(tmp_path)
    frozen = snapshot(root)
    original = module.store_object
    changed = []

    def mutate(*args):
        result = original(*args)
        if not changed:
            changed.append(True)
            (root / "settings.json").write_bytes(b'{"model":"concurrent-source-change"}\n')
        return result

    monkeypatch.setattr(module, "store_object", mutate)
    with pytest.raises(ValueError):
        materialize(root, tmp_path / "review", frozen, {"settings.json": b'{"model":"cheap"}\n'})
    assert read_private(tmp_path / "review/workspaces.json")["status"] == "PREPARATION_FAILED"
    assert (root / "settings.json").read_bytes() == b'{"model":"concurrent-source-change"}\n'


def test_replacement_byte_bound_refuses_before_creating_a_destination(tmp_path):
    from cutback.application_workspaces import MAX_FILE

    root, _ = repository(tmp_path)
    frozen = snapshot(root)
    with pytest.raises(ValueError, match="TRACKED_REPLACEMENTS_REQUIRED"):
        materialize(root, tmp_path / "review", frozen, {"settings.json": b"x" * (MAX_FILE + 1)})
    assert not (tmp_path / "review").exists() and snapshot(root) == frozen


def test_tracked_credential_filename_refuses_whole_export_instead_of_dropping_source(tmp_path):
    root, _ = repository(tmp_path)
    git(root, "add", "--force", ".env")
    git(root, "commit", "-m", "Owned synthetic credential-file refusal fixture")
    frozen = snapshot(root)
    with pytest.raises(ValueError, match="TRACKED_CREDENTIAL_FILES_REQUIRE_SOURCE_REVIEW"):
        materialize(root, tmp_path / "review", frozen, {"settings.json": b'{"model":"cheap"}\n'})
    assert not (tmp_path / "review").exists() and snapshot(root) == frozen


def test_snapshot_bounds_actual_reads_when_file_grows_after_stat(tmp_path, monkeypatch):
    from cutback.application_workspaces import MAX_FILE

    root, _ = repository(tmp_path)
    large = root / "growing.bin"
    large.write_bytes(b"x" * (MAX_FILE + 1))
    git(root, "add", "growing.bin")
    git(root, "commit", "-m", "Owned synthetic changing-size source fixture")
    original = Path.stat

    def earlier_size(path, *args, **kwargs):
        actual = original(path, *args, **kwargs)
        if path == large:
            fields = list(actual)
            fields[6] = 1
            return os.stat_result(fields)
        return actual

    monkeypatch.setattr(Path, "stat", earlier_size)
    with pytest.raises(ValueError, match="SOURCE_BYTES_EXCEEDED"):
        snapshot(root)
