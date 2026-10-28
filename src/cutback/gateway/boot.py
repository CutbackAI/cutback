"""Optional separate gateway process; secrets are never loaded by the collector."""

import os
from uuid import UUID

from pydantic import SecretStr


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
