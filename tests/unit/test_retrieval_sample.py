import json
import re
from pathlib import Path

import pytest

from cutback.core.retrieval import LocalRetrievalIndex, RetrievalCorpus, RetrievalSettings
from cutback.sample_app.retrieval_fixture import fixture


@pytest.mark.parametrize("chunk_chars,expected", [(512, 40), (256, 40), (128, 0)])
def test_independent_policy_families_preserve_labels_and_expose_lost_retrieval(
    chunk_chars, expected
):
    corpus, cases = fixture()
    assert len({case["group"] for case in cases}) == 40
    assert {case["gold"] for case in cases} == {"14", "21", "28"}
    index = LocalRetrievalIndex(
        RetrievalCorpus.model_validate(corpus),
        RetrievalSettings(chunk_chars=chunk_chars, overlap_chars=0, top_k=1),
    )
    correct = 0
    for case in cases:
        passages = index.query(case["input"]).passages
        matches = [re.search(r"\bdeadline-days=(\d+)\b", p.text) for p in passages]
        answer = next((m.group(1) for m in matches if m), "unknown")
        correct += answer == case["gold"]
    assert correct == expected
    root = Path(__file__).resolve().parents[2] / "examples/retrieval-optimization"
    assert json.loads((root / "corpus.json").read_text("utf-8")) == corpus
    assert [
        json.loads(line) for line in (root / "questions.jsonl").read_text("utf-8").splitlines()
    ] == cases
