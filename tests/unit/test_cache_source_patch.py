"""Parse-only source proof, independent business behavior, byte-exact apply and rollback."""

import hashlib
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from cutback.cache_changes import prepare_patch
from cutback.core.inventory import workflow_identity
from cutback.core.request_profiles import prepare_profile
from cutback.evals.experiments import ModelSpec
from cutback.scanner import scan


def prepared(root, *, newline="\n", shadow=""):
    source = '''#!/usr/bin/env python
# coding: utf-8
"""An independent synthetic café workflow."""
from __future__ import annotations
from openai import OpenAI
client = OpenAI()
def classify(text):
    length = len(text)
    answer = client.responses.create(model="gpt-4.1", input=text,
        instructions="Owned synthetic classification task", max_output_tokens=64)
    return answer if length >= 0 else None
'''
    raw = (source + shadow).replace("\n", newline).encode()
    path = root / "app.py"
    path.write_bytes(raw)
    inventory = scan(root)
    site = next(
        item for item in inventory["call_sites"] if item["source_symbol"].endswith("classify")
    )
    workflow, _ = workflow_identity("app.py", "classify")
    profile = prepare_profile(root, site, workflow_id=workflow)
    baseline = ModelSpec(
        key="baseline",
        provider="openai",
        model="gpt-4.1",
        instruction=profile.instruction,
        request_contract=profile.request,
        max_output_tokens=64,
    ).model_dump(mode="json")
    contract = {
        "freshness_ref": "owned-immutable-v1",
        "ttl_seconds": 60,
        "content_retention_authorized": True,
        "immutable_or_safely_invalidated": True,
        "operation_cost_upper_usd": "0.000000001",
        "operation_cost_basis_ref": "owned-fixture",
    }
    recommendation = {
        "id": str(uuid4()),
        "run_id": str(uuid4()),
        "evidence": {
            "candidate": {**baseline, "key": "cache", "exact_cache": contract},
            "rollback": {"baseline": baseline},
            "exact_cache": {"execution_qualified": True},
            "dataset_hash": "1" * 64,
            "plan_hash": "2" * 64,
            "status": "PROMISING",
        },
    }
    return raw, inventory, site, profile, recommendation


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_cache_source_patch_preserves_disabled_native_behavior_and_exact_rollback(
    tmp_path, monkeypatch, newline
):
    raw, inventory, site, profile, recommendation = prepared(tmp_path, newline=newline)
    artifact = prepare_patch(tmp_path, inventory, site, profile, recommendation, proposal_only=True)
    assert (tmp_path / "app.py").read_bytes() == raw
    initialized = subprocess.run(
        ["git", "-c", "init.templateDir=", "init", "--quiet", str(tmp_path)], capture_output=True
    )
    assert initialized.returncode == 0
    for patch, expected in (
        (artifact["diff"], artifact["verification"]["after_sha256"]),
        (artifact["rollback_diff"], hashlib.sha256(raw).hexdigest()),
    ):
        applied = subprocess.run(
            ["git", "-C", str(tmp_path), "-c", "core.autocrlf=false", "apply", "-"],
            input=patch.encode(),
            capture_output=True,
        )
        assert applied.returncode == 0
        current = (tmp_path / "app.py").read_bytes()
        assert hashlib.sha256(current).hexdigest() == expected
        if patch == artifact["diff"]:
            assert current.startswith(b"#!/usr/bin/env python")
            seen, native_response = [], object()

            def native(_seen=seen, _response=native_response, **request):
                _seen.append(request)
                return _response

            client = SimpleNamespace(responses=SimpleNamespace(create=native))
            monkeypatch.setitem(
                sys.modules, "openai", SimpleNamespace(OpenAI=lambda _client=client: _client)
            )
            monkeypatch.delenv("CUTBACK_NATIVE_CACHE_SCOPE_FILE", raising=False)
            namespace = {"__name__": "owned_synthetic_application"}
            exec(compile(current, str(tmp_path / "app.py"), "exec"), namespace)
            assert namespace["classify"]("  synthetic café  ") is native_response
            assert seen == [
                {
                    "model": "gpt-4.1",
                    "input": "  synthetic café  ",
                    "instructions": "Owned synthetic classification task",
                    "max_output_tokens": 64,
                }
            ]
    assert (tmp_path / "app.py").read_bytes() == raw


@pytest.mark.parametrize(
    "shadow",
    [
        "import os as _cutback_cached_responses\n",
        "def unrelated(_cutback_cached_responses):\n    return None\n",
    ],
)
def test_cache_source_adapter_refuses_existing_helper_bindings(tmp_path, shadow):
    _, inventory, site, profile, recommendation = prepared(tmp_path, shadow=shadow)
    with pytest.raises(ValueError, match="HELPER_NAME_CONFLICT"):
        prepare_patch(tmp_path, inventory, site, profile, recommendation, proposal_only=True)


def test_cache_source_adapter_does_not_hide_unevaluated_model_changes(tmp_path):
    _, inventory, site, profile, recommendation = prepared(tmp_path)
    recommendation["evidence"]["candidate"]["model"] = "gpt-4.1-mini"
    with pytest.raises(ValueError, match="MUST_PRESERVE_THE_NATIVE_REQUEST"):
        prepare_patch(tmp_path, inventory, site, profile, recommendation, proposal_only=True)
