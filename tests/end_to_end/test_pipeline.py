"""Actual OpenAI SDK -> HTTP collector -> PostgreSQL -> subprocess CLI.

Inference is a labeled synthetic MockTransport fixture, not a real paid provider call.
"""

import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from uuid import uuid4

import httpx
import openai
import pytest
import uvicorn

import cutback
from cutback.instrumentation import workflow
from cutback.scanner import scan
from cutback.server.api import create_app


@contextmanager
def server(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(app, log_level="error", access_log=False)
    instance = uvicorn.Server(config)
    thread = threading.Thread(target=instance.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not instance.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert instance.started, "local HTTP server did not start"
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        instance.should_exit = True
        thread.join(timeout=5)
        sock.close()


@pytest.mark.postgres
@pytest.mark.e2e
def test_scanner_sdk_http_database_cli_and_restart(postgres_url, monkeypatch, tmp_path):
    path = tmp_path / "main.py"
    path.write_text(
        "from openai import OpenAI\nc=OpenAI()\n"
        'def classify(text):\n    return c.responses.create(model="gpt-4.1-mini", input=text)\n'
    )
    (site,) = scan(tmp_path)["call_sites"]
    assert site["actual_cost"] is None
    admin = secrets.token_urlsafe(48)
    application = create_app(database_url=postgres_url, admin_token=admin, allow_insecure_http=True)
    with (
        server(application) as endpoint,
        httpx.Client(base_url=endpoint, trust_env=False) as control,
    ):
        headers = {"Authorization": f"Bearer {admin}"}
        project = control.post(
            "/v1/projects", headers=headers, json={"slug": "synthetic-e2e-" + uuid4().hex}
        ).json()["id"]
        writer = control.post(
            f"/v1/projects/{project}/keys", headers=headers, json={"role": "ingest"}
        ).json()["token"]
        viewer = control.post(
            f"/v1/projects/{project}/keys", headers=headers, json={"role": "viewer"}
        ).json()["token"]
        monkeypatch.setenv("CUTBACK_PROJECT_TOKEN", writer)
        monkeypatch.delenv("CUTBACK_ENABLED", raising=False)
        observer = cutback.init(
            service_name="synthetic-e2e",
            environment="staging",
            enabled=True,
            endpoint=endpoint + "/v1/ingest/events",
            project_id=project,
        )
        # SDK parses provider-shaped usage fields from synthetic HTTP, including cache/reasoning.
        response = {
            "id": "resp_synthetic_e2e",
            "object": "response",
            "created_at": 1,
            "model": "gpt-4.1-mini-2025-04-14",
            "status": "completed",
            "output": [],
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 100,
                "total_tokens": 1100,
                "input_tokens_details": {"cached_tokens": 200},
                "output_tokens_details": {"reasoning_tokens": 20},
            },
        }
        try:
            with openai.OpenAI(
                api_key="synthetic-not-a-real-key",
                max_retries=0,
                http_client=httpx.Client(
                    transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response))
                ),
            ) as client:
                with workflow("workflow:synthetic-classify", call_site_id=site["call_site_id"]):
                    result = client.responses.create(
                        model="gpt-4.1-mini", input="PRIVATE SYNTHETIC CONTENT"
                    )
                assert result.id == "resp_synthetic_e2e"
            assert observer.flush(2), observer.health()
            assert observer.health()["exported"] == 1
            records = control.get(
                f"/v1/projects/{project}/events", headers={"Authorization": f"Bearer {viewer}"}
            ).json()
            assert len(records) == 1
            assert records[0]["event"]["input_tokens"] == 1000
            assert records[0]["event"]["call_site_id"] == site["call_site_id"]
            assert "PRIVATE" not in json.dumps(records)
            env = {**os.environ, "CUTBACK_CONTROL_TOKEN": viewer}
            cli = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "cutback.cli",
                    "report",
                    "--source",
                    "collector",
                    "--endpoint",
                    endpoint,
                    "--project",
                    project,
                    "--period",
                    "1h",
                ],
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )
            assert cli.returncode == 0, cli.stdout
            report = json.loads(cli.stdout)
            assert report["summary"]["observed_requests"] == 1
            assert report["summary"]["modeled_cost_usd"] == "0.000500000000"
            assert report["semantic_quality"] == "NOT VERIFIED"
        finally:
            observer.close()
    # New collector instance reads the same committed records, without application-local storage.
    from fastapi.testclient import TestClient

    with TestClient(
        create_app(database_url=postgres_url, admin_token=admin, allow_insecure_http=True)
    ) as restarted:
        persisted = restarted.get(
            f"/v1/projects/{project}/usage", headers={"Authorization": f"Bearer {viewer}"}
        ).json()
        assert persisted["summary"]["observed_requests"] == 1
