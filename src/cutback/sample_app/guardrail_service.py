"""Owned synthetic guardrail process; private policy provisioning stays in a pipe."""

import hashlib
import os
import secrets
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from cutback.application_checks import WindowsJob
from cutback.core.guardrails import GuardrailBinding
from cutback.core.tool_policy import canonical
from cutback.guardrail_boot import GuardrailServiceConfig
from cutback.guardrail_client import AsyncGuardrailClient, GuardrailClient
from cutback.guardrail_service import GuardrailClientAuthority


class OwnedGuardrailService:
    """Demo-only owner. Tokens are never in its representation or a config file."""

    def __init__(self, origin, token, binding, workflow):
        self.origin = origin
        self._token = token
        self.binding = binding
        self.workflow = workflow
        self.client = GuardrailClient(
            origin, token, binding=binding, workflow=workflow, allow_insecure_loopback=True
        )
        self._closed = False

    def async_client(self):
        if self._closed:
            raise ValueError("OWNED_GUARDRAIL_SERVICE_CLOSED")
        return AsyncGuardrailClient(
            self.origin,
            self._token,
            binding=self.binding,
            workflow=self.workflow,
            allow_insecure_loopback=True,
        )

    def close(self):
        self._closed = True
        self._token = ""
        self.client.close()


@contextmanager
def owned_guardrail_service(policy, *, workflow, startup_seconds=20, service_id=None):
    """Run one explicitly scoped synthetic policy, with no inherited service keys."""
    if type(startup_seconds) not in {int, float} or not 1 <= startup_seconds <= 30:
        raise ValueError("OWNED_GUARDRAIL_FINITE_STARTUP_REQUIRED")
    token, service_id = secrets.token_urlsafe(48), service_id or uuid4()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = GuardrailServiceConfig(
        service_id=service_id,
        policies=(policy,),
        clients=(
            GuardrailClientAuthority(
                project_id=policy.project_id,
                principal_id=policy.principal_id,
                policy_ids=(policy.policy_id,),
                token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                expires_at=policy.expires_at,
            ),
        ),
        port=port,
        allow_insecure_loopback=True,
    )
    binding = GuardrailBinding(service_id=service_id, **policy.public_metadata())
    owner = OwnedGuardrailService(f"http://127.0.0.1:{port}", token, binding, workflow)
    # Whitelisting removes provider, database, control-plane, proxy and store keys.
    env = {
        name: value
        for name, value in os.environ.items()
        if name.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "PATHEXT"}
    }
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    process = None
    job = WindowsJob() if os.name == "nt" else None
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "cutback.guardrail_boot", "--private-pipe"],
            # The process needs no writable workspace. Keeping its cwd out
            # of the temporary owner directory also avoids a Windows child
            # shutdown race holding that directory open during cleanup.
            cwd=Path(__file__).resolve().parent,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) | 0x4) if job else 0,
        )
        if job:
            job.assign_and_resume(process)
        assert process.stdin is not None
        # EOF completes the bounded boot read. No private bytes go into argv,
        # environment variables, temporary files or process output.
        process.stdin.write(canonical(config.model_dump(mode="json")))
        process.stdin.close()
        deadline = time.monotonic() + startup_seconds
        while True:
            try:
                owner.client.identity()
                break
            except ValueError:
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise ValueError("OWNED_GUARDRAIL_SERVICE_DID_NOT_START") from None
                time.sleep(0.05)
        yield owner
    finally:
        owner.close()
        if job:
            job.close()
        if process is not None and process.poll() is None:
            # This read-only service owns no model calls, stateful tools or DB.
            process.terminate()
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
