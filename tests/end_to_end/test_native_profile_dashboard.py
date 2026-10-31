"""Authenticated profile approval, background analysis and accessible aggregate evidence."""

import json
import os
import subprocess
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from integration.test_cache_workload_assessment import binding
from integration.test_native_request_profiles import retained_profile_stream
from sqlalchemy import select
from support_browser import browser_command
from support_http import owned_collector

from cutback.core.models import LLMEvent
from cutback.server.db import StoredEvent
from cutback.server.native_profile_db import NativeProfileAnalysis
from cutback.server.native_profile_service import run_native_profile_one


@pytest.mark.postgres
def test_request_profile_dashboard_scope_evidence_mobile_and_accessibility(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    event,
):
    if os.getenv("CUTBACK_TEST_BROWSER") != "true":
        pytest.skip("set CUTBACK_TEST_BROWSER=true for actual authenticated profile acceptance")
    sample, store, snapshot = retained_profile_stream(api, tenant, tmp_path, monkeypatch, event)
    source = binding()
    with api[2].state.sessions.begin() as session:
        for row in session.scalars(
            select(StoredEvent).where(StoredEvent.project_id == UUID(tenant[0]))
        ):
            row.event = (
                LLMEvent.model_validate(row.event)
                .model_copy(
                    update={
                        "call_site_id": source.call_site_id,
                        "source_commit": source.source_commit,
                        "traffic_kind": "application",
                    }
                )
                .model_dump(mode="json")
            )
    workload = tmp_path / "owned-workload-input.json"
    workload.write_text(
        json.dumps(
            {
                "source_binding": source.model_dump(mode="json"),
                "baseline_model": event.requested_model,
                "cache_execution": {
                    "freshness_ref": "owned-immutable-v1",
                    "ttl_seconds": 300,
                    "content_retention_authorized": True,
                    "immutable_or_safely_invalidated": True,
                    "operation_cost_upper_usd": "0.000000000001",
                    "operation_cost_basis_ref": "owned-bound",
                },
            }
        ),
        encoding="utf-8",
    )
    stop = threading.Event()

    def worker():
        while not stop.wait(0.05):
            with api[2].state.sessions() as session:
                identifier = session.scalar(
                    select(NativeProfileAnalysis.id)
                    .where(
                        NativeProfileAnalysis.project_id == UUID(tenant[0]),
                        NativeProfileAnalysis.state == "QUEUED",
                    )
                    .limit(1)
                )
            if identifier:
                run_native_profile_one(api[2].state.sessions, store, only_analysis_id=identifier)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    root = Path(__file__).resolve().parents[2]
    token, viewer = api[1]["Authorization"][7:], tenant[2]["Authorization"][7:]
    try:
        with owned_collector(api[2]) as endpoint:
            completed = subprocess.run(
                browser_command(root / "web", "request-profiles.spec.ts"),
                cwd=root / "web",
                env={
                    **os.environ,
                    "CUTBACK_UI_URL": endpoint,
                    "CUTBACK_UI_PROJECT": tenant[0],
                    "CUTBACK_UI_KEY": token,
                    "CUTBACK_PROFILE_POLICY": sample["policy_id"],
                    "CUTBACK_PROFILE_WORKLOAD_INPUT": str(workload),
                    "CUTBACK_PROFILE_START": (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
                    "CUTBACK_PROFILE_END": datetime.now(UTC).isoformat(),
                    "CUTBACK_PROFILE_VIEWER": viewer,
                    "CUTBACK_PROFILE_PRIVATE_FINGERPRINT": snapshot.request_hmac_sha256,
                },
                capture_output=True,
                text=True,
                timeout=180,
            )
        output = (
            (completed.stdout + completed.stderr)
            .replace(token, "<credential-omitted>")
            .replace(
                viewer,
                "<credential-omitted>",
            )
            .replace(snapshot.request_hmac_sha256, "<private-profile-omitted>")
        )
        assert completed.returncode == 0, output
    finally:
        stop.set()
        thread.join(5)
    assert not thread.is_alive()
