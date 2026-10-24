"""Generate local secrets without printing them. Reuse existing files without rotation."""

import os
import secrets
from pathlib import Path

directory = Path(__file__).resolve().parent / "secrets"
directory.mkdir(mode=0o700, exist_ok=True)
for name in ("db_password", "admin_token"):
    path = directory / name
    if not path.exists():
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(secrets.token_urlsafe(48))
print("Local secret files ready. Restrict Windows ACLs to the current user before shared use.")
