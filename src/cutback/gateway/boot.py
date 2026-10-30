"""Optional separate gateway process; secrets are never loaded by the collector."""

import os
from pathlib import Path
from uuid import UUID

from pydantic import SecretStr


def configured_guardrail_clients(projects):
    """One optional operator profile; public binding and separately delivered token."""
    names = (
        "CUTBACK_GATEWAY_GUARDRAIL_BINDING_FILE",
        "CUTBACK_GATEWAY_GUARDRAIL_ORIGIN",
        "CUTBACK_GATEWAY_GUARDRAIL_TOKEN",
        "CUTBACK_GATEWAY_GUARDRAIL_TOKEN_FILE",
    )
    if not any(os.getenv(name) for name in names):
        return {}
    try:
        from cutback.core.guardrails import GuardrailBinding
        from cutback.guardrail_client import AsyncGuardrailClient
        from cutback.guardrails import strict_json

        path = Path(os.environ["CUTBACK_GATEWAY_GUARDRAIL_BINDING_FILE"])
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
            raise ValueError("BOUND")
        binding = GuardrailBinding.model_validate(strict_json(path.read_bytes()))
        if binding.project_id not in projects or len(binding.workflows) != 1:
            raise ValueError("SCOPE")
        if os.getenv("CUTBACK_GATEWAY_GUARDRAIL_TOKEN") and os.getenv(
            "CUTBACK_GATEWAY_GUARDRAIL_TOKEN_FILE"
        ):
            raise ValueError("SINGLE_CREDENTIAL_SOURCE")
        token_file = os.getenv("CUTBACK_GATEWAY_GUARDRAIL_TOKEN_FILE")
        if token_file:
            source = Path(token_file)
            if source.is_symlink() or not source.is_file() or source.stat().st_size > 512:
                raise ValueError("CREDENTIAL_BOUND")
            token = source.read_text(encoding="utf-8").strip()
        else:
            token = os.environ["CUTBACK_GATEWAY_GUARDRAIL_TOKEN"]
        client = AsyncGuardrailClient(
            os.environ["CUTBACK_GATEWAY_GUARDRAIL_ORIGIN"],
            token,
            binding=binding,
            workflow=binding.workflows[0],
            allow_insecure_loopback=os.getenv(
                "CUTBACK_GATEWAY_GUARDRAIL_ALLOW_LOOPBACK_HTTP", "false"
            )
            == "true",
        )
        return {binding.policy_id: client}
    except Exception:
        raise ValueError("GATEWAY_SCOPED_GUARDRAIL_PROFILE_INVALID_OR_UNAVAILABLE") from None


def application():
    from cutback.gateway.app import create_gateway

    if os.getenv("CUTBACK_GATEWAY_ENABLED", "false").lower() != "true":
        return create_gateway()
    from cutback.server.runtime import configure_database, load_secret

    configure_database()
    for name in ("CUTBACK_GATEWAY_OPENAI_KEY", "CUTBACK_GATEWAY_HMAC_KEY"):
        load_secret(name)
    values = os.getenv("CUTBACK_GATEWAY_PROJECTS", "")
    try:
        if len(values) > 37000:
            raise ValueError("allowlist bound")
        projects = frozenset(UUID(value.strip()) for value in values.split(",") if value.strip())
    except ValueError:
        raise ValueError("GATEWAY_EXPLICIT_PROJECT_ALLOWLIST_REQUIRED") from None
    return create_gateway(
        enabled=True,
        database_url=os.environ.get("CUTBACK_DATABASE_URL"),
        provider_key=SecretStr(os.getenv("CUTBACK_GATEWAY_OPENAI_KEY", "")),
        hmac_key=SecretStr(os.getenv("CUTBACK_GATEWAY_HMAC_KEY", "")),
        allowed_project_ids=projects,
        guardrail_clients=configured_guardrail_clients(projects),
    )


def main():
    import uvicorn

    from cutback.server.runtime import trusted_proxies

    uvicorn.run(
        "cutback.gateway.boot:application",
        factory=True,
        host=os.getenv("CUTBACK_GATEWAY_HOST", "127.0.0.1"),
        port=int(os.getenv("CUTBACK_GATEWAY_PORT", "4319")),
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips=trusted_proxies(os.getenv("CUTBACK_TRUSTED_PROXIES", "")),
    )


if __name__ == "__main__":
    main()
