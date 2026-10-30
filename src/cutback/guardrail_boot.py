"""Separate guardrail process, provisioned through a private pipe or encrypted bundle."""

import base64
import ipaddress
import os
import queue
import sys
import threading
from pathlib import Path
from uuid import UUID

from pydantic import Field, StrictBool, model_validator

from cutback.core.guardrails import GuardrailPolicy
from cutback.core.models import StrictModel
from cutback.core.tool_policy import canonical
from cutback.guardrail_service import GuardrailClientAuthority, create_guardrail_service
from cutback.guardrails import strict_json

MAX_CONFIG_BYTES = 8 * 1048576
MAX_BUNDLE_BYTES = 12 * 1048576


class GuardrailServiceConfig(StrictModel):
    version: str = Field(
        default="cutback.guardrail-service.v1", pattern=r"^cutback\.guardrail-service\.v1$"
    )
    service_id: UUID
    policies: tuple[GuardrailPolicy, ...] = Field(min_length=1, max_length=100)
    clients: tuple[GuardrailClientAuthority, ...] = Field(min_length=1, max_length=1000)
    host: str = Field(default="127.0.0.1", max_length=64)
    port: int = Field(default=4321, strict=True, ge=1024, le=65535)
    allow_insecure_loopback: StrictBool = False
    tls_cert_file: str | None = Field(default=None, min_length=1, max_length=4096)
    tls_key_file: str | None = Field(default=None, min_length=1, max_length=4096)
    max_inflight: int = Field(default=8, strict=True, ge=1, le=64)
    max_retained: int = Field(default=1000, strict=True, ge=1, le=10000)
    checks_per_minute: int = Field(default=600, strict=True, ge=1, le=100000)

    @model_validator(mode="after")
    def transport_and_identity(self):
        try:
            address = ipaddress.ip_address(self.host)
        except ValueError:
            raise ValueError("GUARDRAIL_BIND_LITERAL_IP_REQUIRED") from None
        if not self.service_id.int:
            raise ValueError("GUARDRAIL_SERVICE_NONZERO_IDENTITY_REQUIRED")
        if self.allow_insecure_loopback:
            if str(address) != "127.0.0.1" or self.tls_cert_file or self.tls_key_file:
                raise ValueError("GUARDRAIL_PLAINTEXT_REQUIRES_EXPLICIT_LITERAL_LOOPBACK")
        elif not self.tls_cert_file or not self.tls_key_file:
            raise ValueError("GUARDRAIL_TLS_CERTIFICATE_AND_KEY_REQUIRED")
        return self


def encrypt_guardrail_config(config: GuardrailServiceConfig, key: bytes):
    """Return only authenticated ciphertext; the caller owns dedicated key delivery."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if type(key) is not bytes or len(key) != 32:
        raise ValueError("GUARDRAIL_DEDICATED_256_BIT_BUNDLE_KEY_REQUIRED")
    plaintext = canonical(config.model_dump(mode="json"))
    if len(plaintext) > MAX_CONFIG_BYTES:
        raise ValueError("GUARDRAIL_CONFIG_BYTE_BOUND")
    nonce = os.urandom(12)
    aad = b"cutback-guardrail-config-v1:" + config.service_id.bytes
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
    return canonical(
        {
            "version": "cutback.guardrail-bundle.v1",
            "service_id": str(config.service_id),
            "nonce": base64.b64encode(nonce).decode(),
            "ciphertext": base64.b64encode(ciphertext).decode(),
        }
    )


def decrypt_guardrail_config(raw, key: bytes, *, service_id: UUID):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        if (
            type(raw) is not bytes
            or len(raw) > MAX_BUNDLE_BYTES
            or type(key) is not bytes
            or len(key) != 32
        ):
            raise ValueError("BOUND")
        envelope = strict_json(raw)
        if (
            set(envelope) != {"version", "service_id", "nonce", "ciphertext"}
            or envelope["version"] != "cutback.guardrail-bundle.v1"
            or envelope["service_id"] != str(service_id)
        ):
            raise ValueError("IDENTITY")
        nonce = base64.b64decode(envelope["nonce"], validate=True)
        ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
        if len(nonce) != 12 or len(ciphertext) > MAX_CONFIG_BYTES + 16:
            raise ValueError("BOUND")
        plaintext = AESGCM(key).decrypt(
            nonce, ciphertext, b"cutback-guardrail-config-v1:" + service_id.bytes
        )
        config = GuardrailServiceConfig.model_validate(strict_json(plaintext))
        if config.service_id != service_id:
            raise ValueError("IDENTITY")
        return config
    except Exception:
        raise ValueError("GUARDRAIL_ENCRYPTED_CONFIG_INVALID_OR_WRONG_KEY") from None


def private_pipe_config(stream, *, timeout_seconds=10):
    """A stalled provisioning pipe cannot keep the process alive indefinitely."""
    result: queue.Queue[bytes | None] = queue.Queue(maxsize=1)

    def read():
        try:
            value = stream.read(MAX_CONFIG_BYTES + 1)
            result.put(value if type(value) is bytes and len(value) <= MAX_CONFIG_BYTES else None)
        except Exception:
            result.put(None)

    threading.Thread(target=read, daemon=True).start()
    try:
        raw = result.get(timeout=timeout_seconds)
        if raw is None:
            raise ValueError("BOUND")
        return GuardrailServiceConfig.model_validate(strict_json(raw))
    except Exception:
        raise ValueError("GUARDRAIL_PRIVATE_PIPE_CONFIG_INVALID_OR_UNAVAILABLE") from None


def _bundle_key():
    value = os.environ.pop("CUTBACK_GUARDRAIL_BUNDLE_KEY", "")
    path = os.getenv("CUTBACK_GUARDRAIL_BUNDLE_KEY_FILE")
    if path:
        source = Path(path)
        if value or source.is_symlink() or not source.is_file() or source.stat().st_size > 256:
            raise ValueError("GUARDRAIL_SINGLE_DEDICATED_KEY_SOURCE_REQUIRED")
        value = source.read_text(encoding="ascii").strip()
    try:
        decoded = base64.b64decode(value, validate=True)
        if len(decoded) != 32:
            raise ValueError("SIZE")
        return decoded
    except Exception:
        raise ValueError("GUARDRAIL_DEDICATED_256_BIT_BUNDLE_KEY_REQUIRED") from None


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--private-pipe", action="store_true")
    sources.add_argument("--encrypted-config", type=Path)
    parser.add_argument("--service-id", type=UUID)
    args = parser.parse_args()
    try:
        # This role must never inherit another service's provider/control/store keys.
        forbidden = {
            "CUTBACK_DATABASE_URL",
            "CUTBACK_ADMIN_TOKEN",
            "CUTBACK_PAYLOAD_KEY",
            "CUTBACK_EXPERIMENT_OPENAI_KEY",
            "CUTBACK_EXPERIMENT_ANTHROPIC_KEY",
            "CUTBACK_GATEWAY_OPENAI_KEY",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
        }
        if any(os.getenv(name) for name in forbidden):
            raise ValueError("PRIVILEGES")
        if args.private_pipe:
            config = private_pipe_config(sys.stdin.buffer)
            if args.service_id is not None and args.service_id != config.service_id:
                raise ValueError("IDENTITY")
        else:
            path = args.encrypted_config
            if (
                args.service_id is None
                or path.is_symlink()
                or not path.is_file()
                or path.stat().st_size > MAX_BUNDLE_BYTES
            ):
                raise ValueError("ENCRYPTED_CONFIG")
            config = decrypt_guardrail_config(
                path.read_bytes(), _bundle_key(), service_id=args.service_id
            )
        for name in ("tls_cert_file", "tls_key_file"):
            value = getattr(config, name)
            if value:
                path = Path(value)
                if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
                    raise ValueError("TLS")
        app = create_guardrail_service(
            service_id=config.service_id,
            policies=config.policies,
            clients=config.clients,
            max_inflight=config.max_inflight,
            max_retained=config.max_retained,
            checks_per_minute=config.checks_per_minute,
            allow_insecure_loopback=config.allow_insecure_loopback,
        )
        import uvicorn

        uvicorn.run(
            app,
            host=config.host,
            port=config.port,
            ssl_certfile=config.tls_cert_file,
            ssl_keyfile=config.tls_key_file,
            access_log=False,
            proxy_headers=False,
            log_level="critical",
            limit_concurrency=2 * config.max_inflight + 16,
            timeout_keep_alive=5,
            timeout_graceful_shutdown=10,
            ws="none",
            server_header=False,
        )
        return 0
    except Exception:
        print("GUARDRAIL_BOOT_CONFIGURATION_OR_TRANSPORT_UNAVAILABLE", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
