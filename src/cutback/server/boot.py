"""Container entry point. Runtime secrets never appear in argv or application logs."""

import os
from pathlib import Path
from urllib.parse import quote

from cutback.server.migrate import migrate


def main():
    import uvicorn

    from cutback.server.api import create_app

    if "CUTBACK_DATABASE_URL" not in os.environ:
        password = Path(os.environ["CUTBACK_DB_PASSWORD_FILE"]).read_text().strip()
        host = os.environ.get("CUTBACK_DB_HOST", "postgres")
        os.environ["CUTBACK_DATABASE_URL"] = (
            f"postgresql+psycopg://cutback:{quote(password, safe='')}@{host}:5432/cutback"
        )
    if "CUTBACK_ADMIN_TOKEN" not in os.environ:
        os.environ["CUTBACK_ADMIN_TOKEN"] = (
            Path(os.environ["CUTBACK_ADMIN_TOKEN_FILE"]).read_text().strip()
        )
    try:
        migrate()
        app = create_app(allow_insecure_http=os.getenv("CUTBACK_DEV_HTTP", "false") == "true")
    except Exception:
        raise SystemExit(
            "Cutback startup failed; check runtime configuration and migrations (secrets omitted)"
        ) from None
    uvicorn.run(
        app, host="0.0.0.0", port=8080, access_log=False, proxy_headers=False, log_level="warning"
    )


if __name__ == "__main__":
    main()
