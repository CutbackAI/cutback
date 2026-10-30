"""Owned demonstration launcher for a separate credential-isolated broker process."""

import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cutback.tool_client import ToolBrokerClient, ToolBrokerUnavailable
from cutback.tool_isolation import discover_local_endpoint, reconcile_owned_containment


@contextmanager
def owned_tool_service(project, principal, broker_id, image_id, *, workflow):
    token = secrets.token_urlsafe(48)
    endpoint = discover_local_endpoint()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="cutback-owned-tool-broker-") as temporary:
        config = Path(temporary) / "broker.json"
        # Only a key hash is saved. The actual scoped token remains in memory.
        with os.fdopen(
            os.open(config, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w", encoding="utf-8"
        ) as stream:
            json.dump(
                {
                    "broker_id": str(broker_id),
                    "image_id": image_id,
                    "docker_endpoint": endpoint,
                    "host": "127.0.0.1",
                    "port": port,
                    "allow_insecure_loopback": True,
                    "clients": [
                        {
                            "project_id": str(project),
                            "principal_id": str(principal),
                            "workflows": [workflow],
                            "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                        }
                    ],
                },
                stream,
            )
        env = {
            name: value
            for name, value in os.environ.items()
            if name.upper()
            in {
                "PATH",
                "SYSTEMROOT",
                "WINDIR",
                "PATHEXT",
                "TEMP",
                "TMP",
                "HOME",
                "USERPROFILE",
                "HOMEDRIVE",
                "HOMEPATH",
            }
        }
        # The owned launcher follows its own installed/source package, never an
        # application-selected module path. No provider, DB or admin key crosses.
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
        process = subprocess.Popen(
            [sys.executable, "-m", "cutback.tool_broker_boot", "--config", str(config)],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        client = ToolBrokerClient(
            f"http://127.0.0.1:{port}",
            token,
            project_id=project,
            principal_id=principal,
            broker_id=broker_id,
            image_id=image_id,
            allow_insecure_loopback=True,
            timeout_seconds=125,
        )
        try:
            deadline = time.monotonic() + 20
            while True:
                try:
                    client.identity()
                    break
                except ToolBrokerUnavailable:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise ValueError("OWNED_TOOL_BROKER_DID_NOT_START") from None
                    time.sleep(0.05)
            yield client
        finally:
            confirmed = True
            for scope in client.pending_containment_scopes():
                try:
                    client.contain(scope)
                except Exception:
                    confirmed = reconcile_owned_containment(endpoint, scope) and confirmed
            client.close()
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if not confirmed:
                raise ValueError("OWNED_TOOL_CONTAINMENT_UNCONFIRMED_NO_SUCCESS_CLAIM")
