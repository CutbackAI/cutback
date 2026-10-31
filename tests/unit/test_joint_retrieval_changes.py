"""Combined native/settings proposals preserve exact source bytes and reversible scope."""

from uuid import uuid4

import pytest
from support_retrieval_application import application, git

from cutback.core.retrieval import RetrievalSettings
from cutback.evals.experiments import ModelSpec
from cutback.joint_retrieval_changes import JointRetrievalProposal, prepare_patch
from cutback.retrieval_source import inspect_producer


def proposal(root, *, native=True, retrieval=True):
    settings, binding, producer = inspect_producer(root, "app.py")
    baseline = ModelSpec(
        key="baseline",
        provider="openai",
        model=producer.model,
        max_output_tokens=producer.max_output_tokens,
        max_input_tokens=32768,
    )
    return JointRetrievalProposal(
        run_id=uuid4(),
        plan_hash="a" * 64,
        dataset_hash="b" * 64,
        binding=binding,
        producer=producer,
        baseline=baseline,
        proposed=baseline.model_copy(update={"key": "candidate", "max_output_tokens": 64})
        if native
        else baseline.model_copy(update={"key": "candidate"}),
        proposed_settings=RetrievalSettings(chunk_chars=256, overlap_chars=0, top_k=1)
        if retrieval
        else settings,
        decision="synthetic_local_review_only",
    )


@pytest.mark.parametrize("line_ending", [b"\n", b"\r\n"])
@pytest.mark.parametrize("final_newline", [False, True])
def test_combined_patch_and_rollback_preserve_line_endings_and_missing_final_newline(
    tmp_path, line_ending, final_newline
):
    root = application(tmp_path)
    for name in ("app.py", "retrieval.json"):
        raw = (root / name).read_bytes().replace(b"\r\n", b"\n").replace(b"\n", line_ending)
        (root / name).write_bytes(raw if final_newline else raw.rstrip(b"\r\n"))
    git(root, "add", ".")
    if git(root, "diff", "--cached"):
        git(root, "commit", "-m", "Independent line ending fixture")
    original = {
        name: (root / name).read_bytes() for name in ("app.py", "retrieval.json", "corpus.json")
    }
    change = prepare_patch(root, proposal(root))
    assert set(change["proposed_files"]) == {"app.py", "retrieval.json"}
    assert all((root / name).read_bytes() == raw for name, raw in original.items())
    patch = tmp_path / "change.patch"
    patch.write_text(change["diff"], encoding="utf-8", newline="")
    git(root, "apply", str(patch))
    assert b"max_output_tokens=64" in (root / "app.py").read_bytes()
    patch.write_text(change["rollback_diff"], encoding="utf-8", newline="")
    git(root, "apply", str(patch))
    assert all((root / name).read_bytes() == raw for name, raw in original.items())


@pytest.mark.parametrize(
    "native,retrieval,expected", [(True, False, {"app.py"}), (False, True, {"retrieval.json"})]
)
def test_only_semantically_changed_files_are_proposed(tmp_path, native, retrieval, expected):
    root = application(tmp_path)
    source = root / "app.py"
    source.write_bytes(source.read_bytes().replace(b'"gpt-4.1-mini"', b"'gpt-4.1-mini'"))
    git(root, "add", ".")
    git(root, "commit", "-m", "Independent literal style")
    assert (
        set(
            prepare_patch(root, proposal(root, native=native, retrieval=retrieval))[
                "proposed_files"
            ]
        )
        == expected
    )


def test_corpus_change_and_unapproved_model_fields_invalidate_proposal(tmp_path):
    root = application(tmp_path)
    proposed = proposal(root)
    with pytest.raises(ValueError):
        JointRetrievalProposal.model_validate(
            {
                **proposed.model_dump(mode="json"),
                "proposed": {
                    **proposed.proposed.model_dump(mode="json"),
                    "instruction": "unqualified",
                },
            }
        )
    (root / "corpus.json").write_bytes((root / "corpus.json").read_bytes() + b"\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "Independent corpus revision")
    with pytest.raises(ValueError, match="SOURCE_CHANGED"):
        prepare_patch(root, proposed)


def test_joint_cli_inspection_patch_and_private_destination_preflight(tmp_path):
    import json

    import typer
    from typer.testing import CliRunner

    from cutback.cli_retrieval import register_retrieval

    root = application(tmp_path)
    artifact = prepare_patch(root, proposal(root))
    downloaded = tmp_path / "proposal.json"
    downloaded.write_text(json.dumps(artifact))
    app = typer.Typer()

    def fail(*args):
        raise typer.Exit(2)

    register_retrieval(app, None, None, fail, joint=True)
    runner = CliRunner()
    inspection = tmp_path / "inspection.json"
    assert (
        runner.invoke(
            app,
            [
                "model-retrieval",
                "inspect-producer",
                str(root),
                "app.py",
                "--output",
                str(inspection),
            ],
        ).exit_code
        == 0
    )
    assert json.loads(inspection.read_text())["producer_binding"]["file"] == "app.py"
    destination = tmp_path / "change.json"
    command = ["model-retrieval", "patch", str(root), str(downloaded), "--output", str(destination)]
    result = runner.invoke(app, command)
    assert result.exit_code == 0, result.output
    assert json.loads(destination.read_text()) == artifact
    assert destination.with_suffix(".patch").read_bytes() == artifact["diff"].encode()
    artifact["diff"] += "tampered\n"
    downloaded.write_text(json.dumps(artifact))
    assert runner.invoke(app, command).exit_code == 2
    assert json.loads(destination.read_text())["diff"] != artifact["diff"]
    directory = tmp_path / "unsafe.patch"
    directory.mkdir()
    unsafe = [
        "model-retrieval",
        "patch",
        str(root),
        str(downloaded),
        "--output",
        str(tmp_path / "unsafe.json"),
    ]
    assert runner.invoke(app, unsafe).exit_code == 2
    assert not (tmp_path / "unsafe.json").exists()
