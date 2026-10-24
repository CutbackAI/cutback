"""Run all tests against an isolated DB on the local Compose server. No credentials printed."""

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

root = Path(__file__).resolve().parents[1]
secret = root / "infra/compose/secrets/db_password"
env = os.environ.copy()
if "CUTBACK_TEST_DATABASE_URL" not in env and secret.exists():
    password = quote(secret.read_text().strip(), safe="")
    env["CUTBACK_TEST_DATABASE_URL"] = (
        f"postgresql+psycopg://cutback:{password}@127.0.0.1:55432/cutback"
    )
result = subprocess.run([sys.executable, "-m", "pytest", *sys.argv[1:]], cwd=root, env=env)
raise SystemExit(result.returncode)
