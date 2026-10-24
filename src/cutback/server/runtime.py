"""Runtime-only secret loading and explicit reverse-proxy trust; no secret output."""

import ipaddress
import os
from pathlib import Path
from urllib.parse import quote


def load_secret(name):
    if name not in os.environ and os.getenv(name + "_FILE"):
        path = Path(os.environ[name + "_FILE"])
        if not path.is_file() or path.stat().st_size > 16384:
            raise ValueError("RUNTIME_SECRET_FILE_INVALID")
        os.environ[name] = path.read_text(encoding="utf-8").strip()


def configure_database():
    load_secret("CUTBACK_DATABASE_URL")
    if "CUTBACK_DATABASE_URL" not in os.environ:
        load_secret("CUTBACK_DB_PASSWORD")
        password = os.environ["CUTBACK_DB_PASSWORD"]
        host = os.environ.get("CUTBACK_DB_HOST", "postgres")
        user = quote(os.getenv("CUTBACK_DB_USER", "cutback"), safe="")
        database = quote(os.getenv("CUTBACK_DB_NAME", "cutback"), safe="")
        os.environ["CUTBACK_DATABASE_URL"] = (
            f"postgresql+psycopg://{user}:{quote(password, safe='')}@{host}:5432/{database}"
        )


def configure_secrets(*, control=True):
    configure_database()
    names = [
        "CUTBACK_PAYLOAD_KEY",
        "CUTBACK_EXPERIMENT_OPENAI_KEY",
        "CUTBACK_EXPERIMENT_ANTHROPIC_KEY",
        "CUTBACK_LOCAL_INFERENCE_KEY",
    ]
    if control:
        names += [
            "CUTBACK_ADMIN_TOKEN",
            "CUTBACK_OIDC_CLIENT_SECRET",
            "CUTBACK_STRIPE_RESTRICTED_KEY",
            "CUTBACK_STRIPE_WEBHOOK_SECRET",
        ]
    for name in names:
        load_secret(name)


def trusted_proxies(value):
    if not value:
        return ""
    result = []
    for raw in value.split(","):
        address = raw.strip()
        if "/" in address:
            network = ipaddress.ip_network(address, strict=True)
            if network.prefixlen == 0:
                raise ValueError("WILDCARD_PROXY_TRUST_DENIED")
            result.append(str(network))
        else:
            result.append(str(ipaddress.ip_address(address)))
    return ",".join(result)
