"""Review binds an exact clean settings change; it cannot authorize unrelated edits."""

import json
from uuid import uuid4

import pytest
from support_compression_application import application
from support_retrieval_application import git

from cutback.compression_changes import CompressionProposal, inspect_configuration, prepare_patch
from cutback.core.compression import CompressionSettings


@pytest.mark.parametrize("line_ending", [b"\n", b"\r\n"])
@pytest.mark.parametrize("final_newline", [True, False])
def test_compression_settings_patch_is_exact_reversible_and_stale_source_refuses(
    tmp_path, line_ending, final_newline
):
    root = application(tmp_path)
    path = root / "compression.json"
    before = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", line_ending)
    if not final_newline:
        before = before.rstrip(b"\r\n")
    path.write_bytes(before)
    git(root, "add", ".")
    if git(root, "diff", "--cached"):
        git(root, "commit", "-m", "Owned exact byte fixture")
    settings, binding = inspect_configuration(root, "compression.json")
    proposal = CompressionProposal(
        run_id=uuid4(),
        plan_hash="a" * 64,
        dataset_hash="b" * 64,
        binding=binding,
        baseline=settings,
        proposed=CompressionSettings(mode="deduplicate_lines", max_context_bytes=1024),
        decision="synthetic_local_review_only",
    )
    change = prepare_patch(root, proposal)
    assert path.read_bytes() == before
    patch = tmp_path / "review.patch"
    patch.write_text(change["diff"], encoding="utf-8", newline="")
    git(root, "apply", str(patch))
    assert json.loads(path.read_text("utf-8"))["mode"] == "deduplicate_lines"
    patch.write_text(change["rollback_diff"], encoding="utf-8", newline="")
    git(root, "apply", str(patch))
    assert path.read_bytes() == before
    (root / "app.py").write_bytes(
        (root / "app.py").read_bytes() + b"\n# unrelated source mutation\n"
    )
    git(root, "add", ".")
    git(root, "commit", "-m", "Owned stale source fixture")
    with pytest.raises(ValueError, match="SOURCE_CHANGED"):
        prepare_patch(root, proposal)


def test_duplicate_settings_keys_and_hidden_options_cannot_enter_a_source_proposal(tmp_path):
    root = application(tmp_path)
    path = root / "compression.json"
    for value in [
        '{"mode":"identity","mode":"query_lines"}',
        '{"mode":"identity","endpoint":"https://example.invalid"}',
    ]:
        path.write_text(value, encoding="utf-8")
        git(root, "add", ".")
        git(root, "commit", "-m", "Owned invalid settings fixture")
        with pytest.raises(ValueError):
            inspect_configuration(root, "compression.json")


def test_packaged_application_and_question_corpus_match_the_independent_example():
    from pathlib import Path

    from cutback import sample_app
    from cutback.sample_app.compression_fixture import fixture

    example = Path(__file__).resolve().parents[2] / "examples/context-compression"
    assert (example / "app.py").read_bytes() == (
        Path(sample_app.__file__).parent / "compression.py"
    ).read_bytes()
    assert [
        json.loads(line) for line in (example / "questions.jsonl").read_text("utf-8").splitlines()
    ] == fixture()
