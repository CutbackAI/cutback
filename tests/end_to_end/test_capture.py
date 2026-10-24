"""Explicit synthetic capture through an actual SDK and authenticated local HTTP policy gate."""

import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import openai
import pytest
from sqlalchemy import select
from support_provider import SYNTHETIC_RESPONSE
from test_pipeline import server

import cutback
from cutback.evals.artifacts import EncryptedFileStore
from cutback.instrumentation import workflow
from cutback.server.api import create_app
from cutback.server.db import PayloadSample


@pytest.mark.postgres
def test_explicit_sdk_capture_requires_policy_and_keeps_raw_out_of_events(
    postgres_url, monkeypatch, tmp_path
):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    monkeypatch.delenv("CUTBACK_ENABLED", raising=False)
    admin = secrets.token_urlsafe(48)
    app = create_app(database_url=postgres_url, admin_token=admin, allow_insecure_http=True)
    store = EncryptedFileStore(tmp_path, os.urandom(32))
    app.state.artifact_store = store
    with server(app) as endpoint, httpx.Client(base_url=endpoint, trust_env=False) as control:
        headers = {"Authorization": "Bearer " + admin}
        project = control.post(
            "/v1/projects", headers=headers, json={"slug": "synthetic-capture-" + uuid4().hex}
        ).json()["id"]
        token = control.post(
            f"/v1/projects/{project}/keys", headers=headers, json={"role": "ingest"}
        ).json()["token"]
        monkeypatch.setenv("CUTBACK_PROJECT_TOKEN", token)
        policy = control.post(
            f"/v1/projects/{project}/capture-policies",
            headers=headers,
            json={
                "enabled": True,
                "approval_ref": "synthetic-test",
                "rights_basis": "synthetic",
                "environments": ["staging"],
                "workflows": ["classify"],
                "allowed_providers": ["openai"],
                "sample_rate": 1,
                "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            },
        ).json()["id"]
        observer = cutback.init(
            service_name="synthetic",
            environment="staging",
            enabled=True,
            endpoint=endpoint + "/v1/ingest/events",
            project_id=project,
            capture_content=True,
            capture_policy_id=policy,
        )
        response = {
            **SYNTHETIC_RESPONSE,
            "output": [
                {
                    "type": "message",
                    "id": "synthetic-message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": "PRIVATE OUTPUT", "annotations": []}
                    ],
                }
            ],
        }
        try:
            with openai.OpenAI(
                api_key="synthetic",
                max_retries=0,
                http_client=httpx.Client(
                    transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response))
                ),
            ) as client:
                with workflow("classify"):
                    result = client.responses.create(
                        model="gpt-4.1-mini", input="PRIVATE a@example.org"
                    )
                assert result.output_text == "PRIVATE OUTPUT"
            assert observer.flush(3), observer.health()
            assert observer.health()["captured_exported"] == 1
            events = control.get(f"/v1/projects/{project}/events", headers=headers).json()
            assert "PRIVATE" not in json.dumps(events) and "example.org" not in json.dumps(events)
            with app.state.sessions() as session:
                sample = session.scalar(
                    select(PayloadSample).where(PayloadSample.project_id == UUID(project))
                )
                raw = store.get(UUID(project), sample.object_ref)
                assert b"[EMAIL]" in raw and b"PRIVATE OUTPUT" in raw
        finally:
            observer.close()
