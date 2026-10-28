"""Run all tests against an isolated DB on the local Compose server. No credentials printed."""

import os
import re
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
process = subprocess.Popen(
    [sys.executable, "-m", "pytest", *sys.argv[1:], "--tb=short", "--no-showlocals"],
    cwd=root,
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
)
assert process.stdout is not None
for line in process.stdout:
    # A pytest fixture argument or third-party diagnostic must not expose a DSN.
    safe = re.sub(
        r"(postgres(?:ql)?(?:\+[a-z0-9_]+)?://)[^@\s'\"]+@",
        r"\1<credentials-omitted>@",
        line,
        flags=re.IGNORECASE,
    )
    print(safe, end="", flush=True)
raise SystemExit(process.wait())
