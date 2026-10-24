"""Container entry point. Runtime secrets never appear in argv or application logs."""

import os

from cutback.server.migrate import migrate
from cutback.server.runtime import configure_secrets, trusted_proxies


def main():
    import uvicorn

    from cutback.server.api import create_app

    worker = os.getenv("CUTBACK_PROCESS") == "worker"
    try:
        configure_secrets(control=not worker)
        if os.getenv("CUTBACK_SKIP_MIGRATIONS", "false").lower() != "true":
            migrate()
        if worker:
            from cutback.server.worker_boot import main as worker_main

            worker_main()
            return
        app = create_app(allow_insecure_http=os.getenv("CUTBACK_DEV_HTTP", "false") == "true")
        trusted = trusted_proxies(os.getenv("CUTBACK_TRUSTED_PROXIES", ""))
    except Exception:
        raise SystemExit(
            "Cutback startup failed; check runtime configuration and migrations (secrets omitted)"
        ) from None
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8080,
        access_log=False,
        proxy_headers=bool(trusted),
        forwarded_allow_ips=trusted,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
