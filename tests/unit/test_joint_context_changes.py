"""Independent exact consumer, combined change, rollback and both-tree check evidence."""

import json
import shutil
from uuid import uuid4

import pytest
from support_compression_application import application
from support_retrieval_application import git

from cutback.application_checks import ApplicationCommand, prepare, run_checks
from cutback.core.compression import CompressionSettings
from cutback.evals.experiments import ModelSpec
from cutback.joint_context_changes import (
    JointContextProposal,
    inspect_producer,
    prepare_patch,
    review_change,
)


def proposed(root):
    path = root / "app.py"
    path.write_bytes(path.read_bytes().replace(b'model="gpt-4.1-mini"', b'model="gpt-4.1"'))
    git(root, "add", ".")
    if git(root, "diff", "--cached"):
        git(root, "commit", "-m", "Owned qualified higher-cost baseline")
    settings, binding, producer = inspect_producer(root, "app.py")
    model = ModelSpec(
        key="baseline",
        provider="openai",
        model=producer.model,
        max_output_tokens=producer.max_output_tokens,
        max_input_tokens=20000,
    )
    return JointContextProposal(
        run_id=uuid4(),
        plan_hash="a" * 64,
        dataset_hash="b" * 64,
        binding=binding,
        producer=producer,
        baseline=model,
        proposed=model.model_copy(
            update={"key": "compact", "model": "gpt-4.1-mini", "max_output_tokens": 64}
        ),
        proposed_settings=CompressionSettings(
            mode="deduplicate_lines", max_context_bytes=settings.max_context_bytes
        ),
        decision="synthetic_local_review_only",
    )


@pytest.mark.parametrize("ending", [b"\n", b"\r\n"])
def test_exact_consumer_combined_patch_and_rollback(tmp_path, ending):
    root = application(tmp_path)
    path = root / "app.py"
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", ending))
    git(root, "add", ".")
    if git(root, "diff", "--cached"):
        git(root, "commit", "-m", "Owned exact source line endings")
    proposal = proposed(root)
    before = {name: (root / name).read_bytes() for name in ("app.py", "compression.json")}
    artifact = prepare_patch(root, proposal)
    assert set(artifact["proposed_files"]) == {"app.py", "compression.json"}
    patch = tmp_path / "joint.patch"
    patch.write_text(artifact["diff"], encoding="utf-8", newline="")
    git(root, "apply", str(patch))
    assert b"gpt-4.1-mini" in path.read_bytes() and b"max_output_tokens=64" in path.read_bytes()
    assert json.loads((root / "compression.json").read_text())["mode"] == "deduplicate_lines"
    patch.write_text(artifact["rollback_diff"], encoding="utf-8", newline="")
    git(root, "apply", str(patch))
    assert all((root / name).read_bytes() == raw for name, raw in before.items())


def test_qualified_producer_abstains_on_dynamic_or_extra_application_code(tmp_path):
    root = application(tmp_path)
    source = (root / "app.py").read_text()
    for changed in [
        source.replace('model="gpt-4.1-mini"', "model=query"),
        source + '\nraise RuntimeError("OWNED_NEVER_EXECUTE")\n',
        source.replace("store=False", "store=True"),
        source.replace("input=build_input(query, context)", "input=context"),
    ]:
        (root / "app.py").write_text(changed)
        git(root, "add", ".")
        git(root, "commit", "-m", "Owned unsupported producer")
        with pytest.raises(ValueError, match="NOT_QUALIFIED"):
            inspect_producer(root, "app.py")


@pytest.mark.parametrize("name", ["app.py", "compression.json"])
def test_source_mutation_after_inspection_cannot_become_a_joint_patch(tmp_path, monkeypatch, name):
    import cutback.joint_context_changes as changes

    root = application(tmp_path)
    proposal = proposed(root)
    original = changes.inspect_producer

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        path = root / name
        path.write_bytes(path.read_bytes() + b"\n")
        return result

    monkeypatch.setattr(changes, "inspect_producer", changed)
    with pytest.raises(ValueError, match="CHANGED_DURING_PATCH"):
        changes.prepare_patch(root, proposal)


def test_joint_review_requires_actual_both_tree_commands_and_exact_files(tmp_path):
    base = application(tmp_path)
    proposal = proposed(base)
    artifact = prepare_patch(base, proposal)
    target = tmp_path / "proposed"
    shutil.copytree(base, target)
    patch = tmp_path / "change.patch"
    patch.write_text(artifact["diff"], encoding="utf-8", newline="")
    git(target, "apply", str(patch))
    git(target, "add", ".")
    git(target, "commit", "-m", "Owned combined proposed change")
    command = ApplicationCommand(
        key="consumer",
        arguments=[
            "-c",
            "import json; from types import SimpleNamespace as S; from app import answer; "
            "calls=[]; client=S(responses=S(create=lambda **kw: "
            "(calls.append(kw) or S(output_text='owned')))); "
            "context='deadline-days=14\\nbackground\\nbackground\\n'; "
            "assert answer(client,'deadline',context)=='owned'; "
            "assert len(calls)==1 and calls[0]['store'] is False; "
            "assert calls[0]['model'] in {'gpt-4.1','gpt-4.1-mini'}; "
            "assert calls[0]['max_output_tokens'] in {64,128}; "
            "text=calls[0]['input']; frame=json.loads(text[text.index('{'):]); "
            "assert 'deadline-days=14' in frame['context'] and frame['query']=='deadline'",
        ],
    )
    contract = prepare(base, target, [command])
    receipt = run_checks(contract, base, target, execute_local=True)
    review = review_change(
        artifact, contract, receipt, base, target, {"owned-support": ["consumer"]}
    )
    assert review["status"] == "READY_FOR_LOCAL_REVIEW" and not review["remote_published"]
    with pytest.raises(ValueError, match="CONSUMER_CHECK"):
        review_change(artifact, contract, receipt, base, target, {})
    (target / "unrelated.txt").write_text("owned unrelated file")
    git(target, "add", ".")
    git(target, "commit", "-m", "Owned unrelated change")
    extra = prepare(base, target, [command])
    receipt = run_checks(extra, base, target, execute_local=True)
    with pytest.raises(ValueError, match="EXACT_COMBINED"):
        review_change(artifact, extra, receipt, base, target, {"owned-support": ["consumer"]})
