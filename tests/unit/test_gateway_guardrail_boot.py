import asyncio
import json
from uuid import uuid4

import pytest
from unit.test_guardrail_client import binding

from cutback.gateway.boot import configured_guardrail_clients


def configured(monkeypatch, tmp_path):
    _, scope = binding()
    path = tmp_path / "public-binding.json"
    path.write_text(scope.model_dump_json(), encoding="utf-8")
    for name in ("CUTBACK_GATEWAY_GUARDRAIL_TOKEN", "CUTBACK_GATEWAY_GUARDRAIL_TOKEN_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CUTBACK_GATEWAY_GUARDRAIL_BINDING_FILE", str(path))
    monkeypatch.setenv("CUTBACK_GATEWAY_GUARDRAIL_ORIGIN", "https://owned-guard.invalid")
    monkeypatch.setenv("CUTBACK_GATEWAY_GUARDRAIL_TOKEN", "x" * 48)
    return scope, path


def test_explicit_public_binding_separate_memory_credential_and_current_project_scope(
    monkeypatch, tmp_path
):
    scope, path = configured(monkeypatch, tmp_path)
    clients = configured_guardrail_clients({scope.project_id})
    assert clients[scope.policy_id].binding == scope
    assert "content_nonce" not in path.read_text() and "terms" not in path.read_text()
    asyncio.run(clients[scope.policy_id].close())
    with pytest.raises(ValueError, match="PROFILE_INVALID"):
        configured_guardrail_clients({uuid4()})


@pytest.mark.parametrize(
    "damage",
    [
        "credential_userinfo",
        "both_sources",
        "oversized_binding",
        "private_rule_fields",
        "missing_token",
        "duplicate_json",
    ],
)
def test_bad_profile_has_fixed_errors_without_echoing_private_configuration(
    monkeypatch, tmp_path, damage
):
    scope, path = configured(monkeypatch, tmp_path)
    private = "SYNTHETIC_PRIVATE_PROFILE_DETAIL"
    if damage == "credential_userinfo":
        monkeypatch.setenv(
            "CUTBACK_GATEWAY_GUARDRAIL_ORIGIN", "https://" + private + "@owned-guard.invalid"
        )
    if damage == "both_sources":
        monkeypatch.setenv("CUTBACK_GATEWAY_GUARDRAIL_TOKEN_FILE", str(tmp_path / private))
    if damage == "oversized_binding":
        path.write_text(private * 10000)
    if damage == "private_rule_fields":
        path.write_text(json.dumps({**scope.model_dump(mode="json"), "terms": [private]}))
    if damage == "missing_token":
        monkeypatch.delenv("CUTBACK_GATEWAY_GUARDRAIL_TOKEN")
    if damage == "duplicate_json":
        path.write_text('{"service_id":"' + private + '","service_id":"duplicate"}')
    with pytest.raises(ValueError) as error:
        configured_guardrail_clients({scope.project_id})
    assert str(error.value) == "GATEWAY_SCOPED_GUARDRAIL_PROFILE_INVALID_OR_UNAVAILABLE"
    assert private not in repr(error.value)


def test_credential_file_does_not_populate_ambient_environment_or_break_factory_reuse(
    monkeypatch, tmp_path
):
    import os

    scope, _ = configured(monkeypatch, tmp_path)
    secret = tmp_path / "owned-scoped-credential"
    secret.write_text("x" * 48)
    monkeypatch.delenv("CUTBACK_GATEWAY_GUARDRAIL_TOKEN")
    monkeypatch.setenv("CUTBACK_GATEWAY_GUARDRAIL_TOKEN_FILE", str(secret))
    for _ in range(2):
        clients = configured_guardrail_clients({scope.project_id})
        assert "CUTBACK_GATEWAY_GUARDRAIL_TOKEN" not in os.environ
        asyncio.run(clients[scope.policy_id].close())
