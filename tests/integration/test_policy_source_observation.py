"""Actual policy SDK -> observer -> scoped HTTP -> PostgreSQL/profile/report pipeline."""

import asyncio
import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from integration.test_native_request_profiles import profile_capture, queue_analysis
from sqlalchemy import select
from support_http import owned_collector
from support_policy_application import application
from support_remote_agent import remote_environment, remote_interpreter, remote_json
from unit.test_native_cache import native_body

import cutback
from cutback.agent_control import Control
from cutback.agent_policy_workload import assess, source_input
from cutback.application_checks import git
from cutback.core.instrumentation_plan import instrument_plan, refresh_source_map
from cutback.instrumentation import workflow
from cutback.instrumentation.attribution import build_source_map
from cutback.policy_source import inspect_configuration
from cutback.sample_app.policy_fixture import fixture
from cutback.scanner import scan
from cutback.server.db import CapturePolicyRecord, PayloadSample, StoredEvent
from cutback.server.native_profile_service import run_native_profile_one

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("asynchronous", [False, True])
def test_policy_observation_preserves_native_calls_and_source_profiles_through_real_pg(
    api, tenant, tmp_path, monkeypatch, asynchronous
):
    sample, _, _ = profile_capture(api, tenant, tmp_path, monkeypatch, max_samples=12)
    root = application(tmp_path, async_consumer=asynchronous)
    configuration = json.loads((root / "policy.json").read_text("utf-8"))
    configuration["workflow"] = "classify"
    (root / "policy.json").write_text(json.dumps(configuration), encoding="utf-8")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Synthetic",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "Independent observed policy workflow",
    )
    _, binding = inspect_configuration(root, "app.py")
    inventory = scan(root)
    site = next(row for row in inventory["call_sites"] if row["source_file"] == "app.py")
    directory = root / ".cutback"
    directory.mkdir()
    mapping = directory / "source-map.json"
    mapping.write_text(json.dumps(build_source_map(root, inventory)), encoding="utf-8")
    import runpy

    namespace = runpy.run_path(str(root / "app.py"))
    monkeypatch.setenv("CUTBACK_PROJECT_TOKEN", tenant[1]["Authorization"][7:])
    monkeypatch.setenv(
        "CUTBACK_NATIVE_REQUEST_PROFILE_HMAC_KEY", base64.b64encode(os.urandom(32)).decode()
    )
    calls = []

    def provider(request):
        body = json.loads(request.content)
        calls.append(body)
        reply = native_body()
        reply["model"] = body["model"]
        return httpx.Response(200, json=reply)

    with owned_collector(api[2]) as endpoint:
        observer = cutback.init(
            service_name="owned-policy-observation",
            environment="staging",
            enabled=True,
            endpoint=endpoint + "/v1/ingest/events",
            project_id=tenant[0],
            source_map=str(mapping),
            providers=("openai",),
            capture_content=True,
            capture_policy_id=sample["policy_id"],
            timeout_seconds=2,
        )
        try:
            with workflow("classify"):
                if asynchronous:
                    from openai import AsyncOpenAI

                    async def exercise():
                        async with AsyncOpenAI(
                            api_key="owned-synthetic-only",
                            max_retries=0,
                            http_client=httpx.AsyncClient(
                                transport=httpx.MockTransport(provider), trust_env=False
                            ),
                        ) as client:
                            for _ in range(3):
                                result = await namespace["classify"](
                                    client, "owned synthetic repeat"
                                )
                                assert result.status == "completed" and not client.is_closed()

                    asyncio.run(exercise())
                else:
                    from openai import OpenAI

                    with OpenAI(
                        api_key="owned-synthetic-only",
                        max_retries=0,
                        http_client=httpx.Client(
                            transport=httpx.MockTransport(provider), trust_env=False
                        ),
                    ) as client:
                        for _ in range(3):
                            assert (
                                namespace["classify"](client, "owned synthetic repeat").status
                                == "completed"
                            )
                            assert not client.is_closed()
            assert observer.flush(10)
        finally:
            observer.close(10)
    assert len(calls) == 3 and all(body["store"] is False for body in calls)
    with api[2].state.sessions() as session:
        events = session.scalars(
            select(StoredEvent).where(StoredEvent.project_id == UUID(tenant[0]))
        ).all()
        assert len(events) == 3
        assert {row.event["call_site_id"] for row in events} == {site["call_site_id"]}
        assert {row.event["source_commit"] for row in events} == {binding.commit}
        assert "owned synthetic repeat" not in json.dumps([row.event for row in events])
        samples = session.scalars(
            select(PayloadSample).where(PayloadSample.project_id == UUID(tenant[0]))
        ).all()
        assert len(samples) == 3
        assert sum(bool(row.native_profile_ref) for row in samples) == (0 if asynchronous else 3)
    queued, request = queue_analysis(api, tenant, sample)
    assert queued.status_code == 201, queued.text
    assert (
        run_native_profile_one(
            api[2].state.sessions,
            api[2].state.artifact_store,
            only_analysis_id=request["request_id"],
        )["status"]
        == "COMPLETED"
    )
    report = api[0].get(
        f"/v1/projects/{tenant[0]}/native-request-profiles/analyses/{request['request_id']}",
        headers=api[1],
    )
    assert report.status_code == 200, report.text
    assert report.json()["summary"]["profiled_attempts"] == (0 if asynchronous else 3)
    if asynchronous:
        assert report.json()["summary"]["cache_trial_eligible"] is False
    else:
        assert report.json()["summary"]["modeled_potential_hits"] == 2
    assert report.json()["summary"]["production_hit_rate"] is None
    if not asynchronous:
        cache_contract = {
            "freshness_ref": "owned-immutable-v1",
            "ttl_seconds": 300,
            "content_retention_authorized": True,
            "immutable_or_safely_invalidated": True,
            "operation_cost_upper_usd": "0.000000000001",
            "operation_cost_basis_ref": "owned-operation-bound",
        }
        body, _ = source_input(root, producer_file="app.py", cache_execution=cache_contract)
        path = (
            f"/v1/projects/{tenant[0]}/native-request-profiles/analyses/"
            f"{request['request_id']}/source-policy-cache-trial"
        )
        # Assessments join retained metadata without decrypting retained content.
        monkeypatch.setattr(
            api[2].state.artifact_store,
            "get",
            lambda *args: pytest.fail("ASSESSMENT_MUST_NOT_READ_PAYLOADS"),
        )
        with owned_collector(api[2]) as endpoint:
            remote = Control(endpoint, api[1]["Authorization"][7:])
            result = assess(
                root, remote, tenant[0], producer_file="app.py", cache_execution=cache_contract
            )
        assert result["decision"] == "prepare_separate_cache_trial"
        assert result["selected_assessment"]["profiled_attempts"] == 3
        assert result["selected_assessment"]["modeled_potential_hits"] == 2
        assert result["provider_calls"] == result["encrypted_storage_reads"] == 0
        assert result["production_hit_rate"] is None
        assert "OWNED_PRIVATE_POLICY_INSTRUCTION" not in json.dumps(result)
        assert len(calls) == 3
        configured = os.getenv("CUTBACK_TEST_REMOTE_PYTHON")
        if configured:
            # A fresh core-plus-HTTPX client has no SDK/server/crypto installation.
            executable = remote_interpreter(configured)
            environment = remote_environment()
            admin = api[1]["Authorization"][7:]
            environment["CUTBACK_CONTROL_TOKEN"] = admin
            private_contract = tmp_path / "owned-cache-contract.json"
            private_contract.write_text(json.dumps(cache_contract), encoding="utf-8")
            with owned_collector(api[2]) as endpoint:
                child = subprocess.run(
                    [
                        str(executable),
                        "-I",
                        "-m",
                        "cutback.cli",
                        "source-policy",
                        "assess-workload",
                        "--target",
                        str(root),
                        "--producer",
                        "app.py",
                        "--endpoint",
                        endpoint,
                        "--project",
                        tenant[0],
                        "--exact-cache-contract",
                        str(private_contract),
                    ],
                    cwd=tmp_path,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=45,
                )
            assert admin not in child.stdout + child.stderr
            assert "OWNED_PRIVATE_POLICY_INSTRUCTION" not in child.stdout + child.stderr
            assert child.returncode == 0, "MINIMAL_POLICY_WORKLOAD_ASSESSMENT_FAILED"
            minimal = remote_json(child)
            assert minimal["selected_assessment"] == result["selected_assessment"]
            assert minimal["decision"] == "prepare_separate_cache_trial"
        assert api[0].post(path, headers=tenant[1], json=body).status_code == 403
        assert api[0].post(path, headers=tenant[2], json=body).status_code == 403
        other = api[0].post("/v1/projects", headers=api[1], json={"slug": "other-" + uuid4().hex})
        assert other.status_code == 201
        assert (
            api[0]
            .post(path.replace(tenant[0], other.json()["id"]), headers=api[1], json=body)
            .status_code
            == 404
        )
        old_path = path.replace("/source-policy-cache-trial", "/cache-trial")
        assert api[0].post(old_path, headers=api[1], json=body).status_code == 422
        # A late native event changes the census even within the original window.
        with api[2].state.sessions.begin() as session:
            row = session.scalars(
                select(StoredEvent).where(StoredEvent.project_id == UUID(tenant[0]))
            ).first()
            late_event = {**row.event, "event_id": str(uuid4())}
        late = api[0].post(
            "/v1/ingest/events",
            headers=tenant[1],
            json={"project_id": tenant[0], "events": [late_event]},
        )
        assert late.status_code == 200
        stale = api[0].post(path, headers=api[1], json=body)
        assert stale.status_code == 200
        assert stale.json()["decision"] == "abstain"
        assert "RETAINED_METADATA_SNAPSHOT_UNAVAILABLE_OR_CHANGED" in stale.json()["reasons"]
        with api[2].state.sessions.begin() as session:
            session.get(CapturePolicyRecord, UUID(sample["policy_id"])).enabled = False
        assert api[0].post(path, headers=api[1], json=body).status_code == 403
    exported = api[0].get(f"/v1/projects/{tenant[0]}/export", headers=api[1])
    assert exported.status_code == 200 and "owned synthetic repeat" not in exported.text
    assert "OWNED_PRIVATE_POLICY_INSTRUCTION" not in exported.text


@pytest.mark.parametrize("asynchronous", [False, True])
def test_reviewed_external_sample_startup_collects_only_metadata_in_an_independent_process(
    api, tenant, tmp_path, asynchronous
):
    root = tmp_path / "independent-observed-policy"
    root.mkdir()
    for name, value in fixture(async_consumer=asynchronous).items():
        (root / name).write_text(value, encoding="utf-8")
    producer = (root / "app.py").read_bytes()
    proposal = instrument_plan(root, entrypoint=Path("observe_application.py"))
    (root / "observe_application.py").write_text(proposal["after"], encoding="utf-8")
    directory = root / ".cutback"
    directory.mkdir()
    # Commit the reviewed external bootstrap before producing the final source map.
    git(root, "init", "--template=")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Synthetic",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "Independent reviewed observation startup",
    )
    source = inspect_configuration(root, "app.py")[1]
    assert refresh_source_map(root)["source_commit"] == source.commit
    with owned_collector(api[2]) as endpoint:
        environment = {
            **os.environ,
            "CUTBACK_ENABLED": "true",
            "CUTBACK_SERVICE_NAME": "independent-policy-observation",
            "CUTBACK_ENVIRONMENT": "staging",
            "CUTBACK_ENDPOINT": endpoint + "/v1/ingest/events",
            "CUTBACK_PROJECT_ID": tenant[0],
            "CUTBACK_PROJECT_TOKEN": tenant[1]["Authorization"][7:],
        }
        result = subprocess.run(
            [sys.executable, "observe_application.py"],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=45,
        )
    # Never put child output or its in-memory credential in assertion diagnostics.
    assert result.returncode == 0, "OWNED_EXTERNAL_OBSERVATION_PROCESS_FAILED"
    receipt = json.loads(result.stdout)
    assert receipt["native_sdk_calls"] == 7 and receipt["paid_provider_calls"] == 0
    assert receipt["metadata_flush_complete"] is True
    assert receipt["observer_health"]["enabled"] is True
    assert (root / "app.py").read_bytes() == producer
    with api[2].state.sessions() as session:
        events = session.scalars(
            select(StoredEvent).where(StoredEvent.project_id == UUID(tenant[0]))
        ).all()
        assert len(events) == 7
        assert {row.event["source_commit"] for row in events} == {source.commit}
        assert {row.event["workflow_id"] for row in events} == {source.workflow}
        assert all(row.event["call_site_id"] != "unattributed" for row in events)
        assert not session.scalars(
            select(PayloadSample).where(PayloadSample.project_id == UUID(tenant[0]))
        ).first()
        assert "Priority: urgent." not in json.dumps([row.event for row in events])
