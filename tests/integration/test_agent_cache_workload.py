"""Independent committed application → native SDK → encrypted analysis → agent assessment."""

import base64
import importlib.util
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from openai import OpenAI
from support_agent import fresh_application
from support_http import owned_collector
from unit.test_native_cache import native_body

import cutback
from cutback.agent import analyze, load_session, selected_profile
from cutback.agent_cache_workload import assess
from cutback.agent_control import Control
from cutback.evals.artifacts import EncryptedFileStore
from cutback.instrumentation.attribution import build_source_map
from cutback.scanner import scan
from cutback.server.db import StoredEvent
from cutback.server.native_profile_service import run_native_profile_one

pytestmark = pytest.mark.postgres


def committed_application(root):
    root.mkdir()
    fresh_application(root)
    path = root / "app.py"
    path.write_text(
        path.read_text(encoding="utf-8")
        .replace(
            "OpenAI()",
            "OpenAI(max_retries=0)",
        )
        .replace("max_output_tokens=64)", "max_output_tokens=64, store=False)"),
        encoding="utf-8",
    )
    (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    for args in (
        ("init",),
        ("add", "app.py", "cases.jsonl", ".gitignore"),
        (
            "-c",
            "user.name=Owned Fixture",
            "-c",
            "user.email=fixture@example.test",
            "-c",
            "core.hooksPath=",
            "-c",
            "commit.gpgSign=false",
            "commit",
            "-m",
            "Owned baseline",
        ),
    ):
        result = subprocess.run(["git", *args], cwd=root, capture_output=True, timeout=20)
        assert result.returncode == 0, "OWNED_SOURCE_COMMIT_FAILED"
    return root


def test_agent_derives_source_scope_and_finds_real_observed_profile_without_manual_ids(
    api,
    tenant,
    tmp_path,
    monkeypatch,
):
    root = committed_application(tmp_path / "application")
    state = analyze(root)
    profile = selected_profile(root, state)
    original = (root / "app.py").read_bytes()
    source_map = root / ".cutback/source-map.json"
    source_map.write_text(json.dumps(build_source_map(root, scan(root))), encoding="utf-8")
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    monkeypatch.setenv("CUTBACK_PROJECT_TOKEN", tenant[1]["Authorization"][7:])
    monkeypatch.setenv(
        "CUTBACK_NATIVE_REQUEST_PROFILE_HMAC_KEY", base64.b64encode(os.urandom(32)).decode()
    )
    monkeypatch.setenv("OPENAI_API_KEY", "owned-synthetic-source-provider")
    store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    api[2].state.artifact_store = store
    workflow = profile.binding.workflow_id
    policy = api[0].post(
        f"/v1/projects/{tenant[0]}/capture-policies",
        headers=api[1],
        json={
            "enabled": True,
            "approval_ref": "owned-source-workload",
            "rights_basis": "synthetic",
            "environments": ["staging"],
            "workflows": [workflow],
            "allowed_providers": ["openai"],
            "sample_rate": 1,
            "max_samples": 8,
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "native_request_profile": {
                "freshness_ref": "owned-immutable-source",
                "prospective_ttl_seconds": 300,
                "fingerprint_capture_authorized": True,
                "immutable_or_safely_invalidated": True,
            },
        },
    )
    assert policy.status_code == 201
    calls = []

    def provider(request):
        calls.append(1)
        return httpx.Response(200, json=native_body())

    prior = sys.modules.get("app")
    try:
        with owned_collector(api[2]) as endpoint:
            observer = cutback.init(
                service_name="owned-source-workload",
                environment="staging",
                enabled=True,
                endpoint=endpoint + "/v1/ingest/events",
                project_id=tenant[0],
                capture_content=True,
                capture_policy_id=policy.json()["id"],
                source_map=str(source_map),
                timeout_seconds=2,
            )
            try:
                spec = importlib.util.spec_from_file_location("app", root / "app.py")
                module = importlib.util.module_from_spec(spec)
                sys.modules["app"] = module
                spec.loader.exec_module(module)
                module.client.close()
                with OpenAI(
                    api_key="owned-synthetic-source-provider",
                    max_retries=0,
                    http_client=httpx.Client(
                        transport=httpx.MockTransport(provider), trust_env=False
                    ),
                ) as native:
                    module.client = native
                    for _ in range(4):
                        assert module.classify("Owned identical billing request").output_text
                assert observer.flush(5), observer.health()
                assert observer.health()["native_profile_exported"] == 4
            finally:
                observer.close()
    finally:
        if prior is None:
            sys.modules.pop("app", None)
        else:
            sys.modules["app"] = prior
    assert len(calls) == 4
    with api[2].state.sessions() as session:
        events = session.query(StoredEvent).filter_by(project_id=UUID(tenant[0])).all()
        assert len(events) == 4
        assert all(item.event["call_site_id"] == profile.binding.call_site_id for item in events)
        assert all(item.event["source_commit"] == profile.binding.source_commit for item in events)
    from uuid import uuid4

    now = datetime.now(UTC)
    request_id = str(uuid4())
    queued = api[0].post(
        f"/v1/projects/{tenant[0]}/native-request-profiles/analyses",
        headers=api[1],
        json={
            "request_id": request_id,
            "policy_id": policy.json()["id"],
            "environment": "staging",
            "workflow": workflow,
            "purpose": "owned-source-assessment",
            "window_start": (now - timedelta(minutes=5)).isoformat(),
            "window_end": now.isoformat(),
        },
    )
    assert queued.status_code == 201
    assert (
        run_native_profile_one(api[2].state.sessions, store, only_analysis_id=request_id)["status"]
        == "COMPLETED"
    )
    monkeypatch.setattr(store, "get", lambda *args: pytest.fail("ASSESSMENT_MUST_NOT_READ_CONTENT"))
    control = Control("http://127.0.0.1", api[1]["Authorization"][7:], client=api[0])
    result = assess(
        root,
        control,
        tenant[0],
        cache_execution={
            "freshness_ref": "owned-immutable-source",
            "ttl_seconds": 300,
            "content_retention_authorized": True,
            "immutable_or_safely_invalidated": True,
            "operation_cost_upper_usd": "0.000000000001",
            "operation_cost_basis_ref": "owned-file-bound",
        },
    )
    assert result["decision"] == "prepare_separate_cache_trial"
    assert result["selected_assessment"]["analysis_id"] == request_id
    assert result["selected_assessment"]["modeled_potential_hits"] == 3
    assert result["paid_calls_authorized"] is False and result["source_change_authorized"] is False
    assert result["provider_calls"] == result["encrypted_storage_reads"] == 0
    assert load_session(root)["phase"] == "ANALYZED"
    assert load_session(root)["workload_assessments"][-1] == result
    assert (root / "app.py").read_bytes() == original
    assert len(calls) == 4
