import json
from pathlib import Path
from uuid import uuid4

import pytest
from support_retrieval_application import application, git

from cutback.core.retrieval import RetrievalSettings
from cutback.retrieval_changes import RetrievalProposal, inspect_configuration, prepare_patch


def proposal(root):
    settings, binding = inspect_configuration(root, "retrieval.json")
    return RetrievalProposal(
        run_id=uuid4(),
        plan_hash="a" * 64,
        dataset_hash="b" * 64,
        binding=binding,
        baseline=settings,
        proposed=RetrievalSettings(chunk_chars=256, overlap_chars=0, top_k=1),
        decision="synthetic_local_review_only",
    )


@pytest.mark.parametrize("final_newline", [True, False])
@pytest.mark.parametrize("line_ending", [b"\n", b"\r\n"])
def test_exact_diff_applies_and_rolls_back_without_changing_source_during_preparation(
    tmp_path, final_newline, line_ending
):
    root = application(tmp_path)
    file = root / "retrieval.json"
    file.write_bytes(file.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", line_ending))
    if not final_newline:
        file.write_bytes(file.read_bytes().rstrip(b"\r\n"))
    git(root, "add", ".")
    if git(root, "diff", "--cached"):
        git(root, "commit", "-m", "Owned line-ending fixture")
    before = file.read_bytes()
    result = prepare_patch(root, proposal(root))
    assert file.read_bytes() == before
    patch = tmp_path / "change.patch"
    patch.write_text(result["diff"], encoding="utf-8", newline="")
    git(root, "apply", str(patch))
    assert json.loads(file.read_text("utf-8"))["chunk_chars"] == 256
    patch.write_text(result["rollback_diff"], encoding="utf-8", newline="")
    git(root, "apply", str(patch))
    assert file.read_bytes() == before


def test_source_settings_only_binding_rejects_duplicate_keys_and_stale_committed_source(tmp_path):
    root = application(tmp_path)
    value = proposal(root)
    file = root / "app.py"
    file.write_bytes(file.read_bytes() + b"\n# concurrent source change\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "Source changed after approval")
    with pytest.raises(ValueError, match="SOURCE_CHANGED"):
        prepare_patch(root, value)
    (root / "retrieval.json").write_text('{"chunk_chars":512,"chunk_chars":128}', encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "Owned duplicate-key negative fixture")
    with pytest.raises(ValueError, match="DUPLICATE_JSON_KEY"):
        inspect_configuration(root, "retrieval.json")


def test_packaged_sample_source_matches_independent_application():
    from cutback import sample_app

    source = Path(__file__).resolve().parents[2] / "examples/retrieval-optimization/app.py"
    assert source.read_bytes() == (Path(sample_app.__file__).parent / "retrieval.py").read_bytes()
