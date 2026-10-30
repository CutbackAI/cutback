import base64
import hashlib
import io
import json
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from unit.test_guardrails import policy

from cutback.guardrail_boot import (
    GuardrailServiceConfig,
    decrypt_guardrail_config,
    encrypt_guardrail_config,
    main,
    private_pipe_config,
)


def config(**changes):
    definition = policy()
    return GuardrailServiceConfig.model_validate(
        {
            "service_id": str(uuid4()),
            "policies": [definition.model_dump(mode="json")],
            "clients": [
                {
                    "project_id": str(definition.project_id),
                    "principal_id": str(definition.principal_id),
                    "token_sha256": hashlib.sha256(
                        b"owned-synthetic-guard-client-never-sent"
                    ).hexdigest(),
                    "policy_ids": [str(definition.policy_id)],
                    "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
                }
            ],
            "allow_insecure_loopback": True,
            **changes,
        }
    )


def test_encrypted_bundle_contains_no_policy_literals_and_binds_exact_service_and_key():
    source = config()
    key = b"x" * 32
    raw = encrypt_guardrail_config(source, key)
    assert (
        b"SYNTHETIC_DENIED_LITERAL" not in raw
        and str(source.policies[0].content_nonce).encode() not in raw
    )
    assert decrypt_guardrail_config(raw, key, service_id=source.service_id) == source
    for candidate_key, service_id in ((b"y" * 32, source.service_id), (key, uuid4())):
        with pytest.raises(ValueError, match="ENCRYPTED_CONFIG_INVALID") as failure:
            decrypt_guardrail_config(raw, candidate_key, service_id=service_id)
        assert "SYNTHETIC_DENIED" not in str(failure.value)
    value = json.loads(raw)
    ciphertext = bytearray(base64.b64decode(value["ciphertext"]))
    ciphertext[-1] ^= 1
    value["ciphertext"] = base64.b64encode(ciphertext).decode()
    with pytest.raises(ValueError, match="ENCRYPTED_CONFIG_INVALID"):
        decrypt_guardrail_config(json.dumps(value).encode(), key, service_id=source.service_id)


def test_private_pipe_is_bounded_and_does_not_require_a_plaintext_policy_file():
    source = config()
    assert private_pipe_config(io.BytesIO(source.model_dump_json().encode())) == source
    for raw in (
        b"not JSON: SYNTHETIC_PRIVATE_DETAIL",
        b'{"service_id":"PRIVATE","service_id":"DUPLICATE"}',
    ):
        with pytest.raises(ValueError, match="PRIVATE_PIPE_CONFIG_INVALID") as failure:
            private_pipe_config(io.BytesIO(raw))
        assert "SYNTHETIC_PRIVATE" not in str(failure.value)
    release = threading.Event()

    class Stalled:
        def read(self, count):
            assert release.wait(2)
            return b""

    try:
        with pytest.raises(ValueError, match="PRIVATE_PIPE_CONFIG_INVALID"):
            private_pipe_config(Stalled(), timeout_seconds=0.01)
    finally:
        release.set()


@pytest.mark.parametrize(
    "changes", [{"host": "0.0.0.0"}, {"host": "localhost"}, {"allow_insecure_loopback": False}]
)
def test_boot_does_not_allow_accidental_public_plaintext(changes):
    with pytest.raises(ValueError):
        config(**changes)


def test_boot_rejects_inherited_provider_privilege_before_reading_content(monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "SYNTHETIC_PARENT_CREDENTIAL")
    monkeypatch.setattr("sys.argv", ["owned-guardrail-service", "--private-pipe"])
    monkeypatch.setattr("sys.stdin", SimpleNamespace(buffer=io.BytesIO(b"PRIVATE_UNREAD_CONFIG")))
    assert main() == 2
    assert "SYNTHETIC_PARENT_CREDENTIAL" not in capsys.readouterr().err


def test_owned_loopback_boot_disables_access_logs_proxies_websockets_and_multiple_workers(
    monkeypatch,
):
    source = config()
    for name in (
        "CUTBACK_DATABASE_URL",
        "CUTBACK_ADMIN_TOKEN",
        "CUTBACK_PAYLOAD_KEY",
        "CUTBACK_EXPERIMENT_OPENAI_KEY",
        "CUTBACK_EXPERIMENT_ANTHROPIC_KEY",
        "CUTBACK_GATEWAY_OPENAI_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("sys.argv", ["owned-guardrail-service", "--private-pipe"])
    monkeypatch.setattr(
        "sys.stdin", SimpleNamespace(buffer=io.BytesIO(source.model_dump_json().encode()))
    )
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: calls.append(kwargs))
    assert main() == 0
    assert len(calls) == 1 and calls[0]["host"] == "127.0.0.1"
    assert calls[0]["proxy_headers"] is False and calls[0]["access_log"] is False
    assert calls[0]["ws"] == "none" and "workers" not in calls[0]
