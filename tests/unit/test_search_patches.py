import ast
import hashlib

import pytest

from cutback.changes import model_patch
from cutback.core.request_profiles import prepare_profile
from cutback.scanner import scan


@pytest.mark.parametrize("surface", ["responses", "chat", "anthropic"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_evaluated_cap_patch_changes_only_exact_literal_and_preserves_original(
    tmp_path,
    surface,
    newline,
):
    source = {
        "responses": (
            "from openai import OpenAI\nc=OpenAI()\ndef classify(text):\n"
            '    unused=64\n    return c.responses.create(model="gpt-4.1", '
            "input=text, max_output_tokens=64)\n"
        ),
        "chat": (
            "from openai import OpenAI\nc=OpenAI()\ndef classify(text):\n"
            '    unused=64\n    return c.chat.completions.create(model="gpt-4.1", '
            'messages=[{"role":"user","content":text}], max_tokens=64)\n'
        ),
        "anthropic": (
            "from anthropic import Anthropic\nc=Anthropic()\ndef classify(text):\n"
            '    unused=64\n    return c.messages.create(model="claude-sonnet-4-6", '
            'messages=[{"role":"user","content":text}], max_tokens=64)\n'
        ),
    }[surface]
    before = source.replace("\n", newline).encode()
    path = tmp_path / "app.py"
    path.write_bytes(before)
    inventory = scan(tmp_path)
    site = inventory["call_sites"][0]
    profile = prepare_profile(tmp_path, site)
    recommendation = {
        "id": "owned-rec",
        "run_id": "owned-run",
        "status": "pending",
        "evidence": {
            "evaluator_version": "deterministic-v3",
            "lineage": {"contract": "cutback.lineage.v1", "fresh_test_claim": True},
            "quality_gate_passed": True,
            "cheaper_on_sample": True,
            "source_binding": profile.binding.model_dump(mode="json"),
            "rollback": {"baseline": profile.baseline()},
            "candidate": {**profile.baseline(), "key": "smaller-cap", "max_output_tokens": 32},
            "dataset_hash": "a" * 64,
            "plan_hash": "b" * 64,
            "status": "PROMISING",
        },
    }
    result = model_patch(
        tmp_path,
        inventory,
        site["call_site_id"],
        recommendation,
        expected_source_sha256=hashlib.sha256(before).hexdigest(),
        proposal_only=True,
    )
    assert result["transformations"] == ["output_cap"]
    assert "unused=64" in result["diff"]
    assert "tokens=32" in result["diff"]
    assert "tokens=64" in result["rollback_diff"]
    assert path.read_bytes() == before
    ast.parse(source)
    recommendation["evidence"]["candidate"]["max_output_tokens"] = 128
    with pytest.raises(ValueError, match="EVALUATED_REDUCTION"):
        model_patch(
            tmp_path,
            inventory,
            site["call_site_id"],
            recommendation,
            expected_source_sha256=hashlib.sha256(before).hexdigest(),
            proposal_only=True,
        )
