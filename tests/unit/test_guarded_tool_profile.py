"""Scoped worker configuration must fail before any checker or provider request."""

import json
from uuid import uuid4

import pytest
from unit.test_guarded_tool_contract import guarded_plan

from cutback.server.tool_execution import configured_tool_guard_client


@pytest.mark.parametrize("failure", [None, "owner", "both_tokens", "http", "oversize", "workflow"])
def test_worker_guard_profile_requires_exact_public_binding_and_one_fixed_credential_source(
    tmp_path, monkeypatch, failure
):
    _, plan = guarded_plan()
    spec = plan.episode_execution.isolated_tools
    raw = spec.runtime_guardrails.model_dump(mode="json")
    if failure == "owner":
        raw["principal_id"] = str(uuid4())
    path, token_file = tmp_path / "binding.json", tmp_path / "token"
    path.write_text(json.dumps(raw), encoding="utf-8")
    token = "owned-private-guard-token-" + "x" * 40
    token_file.write_text(token if failure != "oversize" else token * 100, encoding="utf-8")
    monkeypatch.setenv("CUTBACK_TOOL_GUARDRAIL_BINDING_FILE", str(path))
    monkeypatch.setenv(
        "CUTBACK_TOOL_GUARDRAIL_ORIGIN",
        "http://example.invalid" if failure == "http" else "https://owned-guard.invalid",
    )
    monkeypatch.setenv("CUTBACK_TOOL_GUARDRAIL_TOKEN_FILE", str(token_file))
    monkeypatch.delenv("CUTBACK_TOOL_GUARDRAIL_TOKEN", raising=False)
    if failure == "both_tokens":
        monkeypatch.setenv("CUTBACK_TOOL_GUARDRAIL_TOKEN", token)
    workflow = "different" if failure == "workflow" else plan.workflow
    if failure is not None:
        with pytest.raises(
            ValueError, match="^GUARDED_TOOL_FIXED_SCOPED_CLIENT_NOT_CONFIGURED$"
        ) as result:
            configured_tool_guard_client(spec, workflow)
        assert token not in str(result.value)
    else:
        client = configured_tool_guard_client(spec, workflow)
        try:
            assert client.binding == spec.runtime_guardrails and client.workflow == workflow
            assert "owned-private" not in repr(client)
        finally:
            client.close()
