"""Generate the dashboard's API schema without connecting to a database or exposing secrets."""

import json
import subprocess
import sys
from pathlib import Path

from cutback.server.api import create_app

application = create_app(
    database_url="postgresql+psycopg://unused@127.0.0.1/unused",
    admin_token="synthetic-schema-only-" + "x" * 32,
)
destination = Path(__file__).resolve().parents[1] / "web/openapi.json"
destination.write_text(json.dumps(application.openapi(), indent=2) + "\n", encoding="utf-8")
application.state.engine.dispose()
print("Versioned OpenAPI contract exported; no network connection made.")

subprocess.run(
    [sys.executable, str(destination.parent.parent / "scripts/generate_ts_client.py")], check=True
)
