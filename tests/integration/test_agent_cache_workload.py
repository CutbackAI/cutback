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


@pytest.mark.parametrize("portfolio", [False, True])
def test_agent_derives_source_scope_and_finds_real_observed_profile_without_manual_ids(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    portfolio,
):
    root = committed_application(tmp_path / "application")
    if portfolio:
        cases = []
        for group in range(20):
            family = chr(65 + group)
            for repeat in range(10):
                cases.append(
                    {
                        "id": f"{family}-{repeat}",
                        "input": f"Owned topic {family}",
                        "gold": "A",
                        "group": f"family-{family}",
                    }
                )
        (root / "cases.jsonl").write_text(
            "".join(json.dumps(case) + "\n" for case in cases), encoding="utf-8"
        )
        for args in (
            ("add", "cases.jsonl"),
            (
                "-c",
                "user.name=Owned Fixture",
                "-c",
                "user.email=fixture@example.test",
                "-c",
                "core.hooksPath=",
                "commit",
                "-m",
                "Independent repeated evaluation cases",
            ),
        ):
            subprocess.run(["git", *args], cwd=root, capture_output=True, check=True, timeout=20)
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
                assert observer.flush(20), observer.health()
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
    read_content = store.get
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
    if portfolio:
        monkeypatch.setattr(store, "get", read_content)
        monkeypatch.setenv("CUTBACK_CACHE_ENABLED", "true")
        monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "owned-synthetic-only")
        exercise_native_portfolio(root, control, api, tenant, result, original, monkeypatch)


def exercise_native_portfolio(root, control, api, tenant, assessment, original, monkeypatch):
    from support_agent import native_response

    from cutback.agent import advance, confirm, gate, prepare, proposal
    from cutback.evals.executors import OpenAIExecutor
    from cutback.server.worker import run_one

    contract = {
        "freshness_ref": "owned-immutable-source",
        "ttl_seconds": 300,
        "content_retention_authorized": True,
        "immutable_or_safely_invalidated": True,
        "operation_cost_upper_usd": "0.000000000001",
        "operation_cost_basis_ref": "owned-file-bound",
    }
    parameters = dict(
        files=["cases.jsonl"],
        rights_basis="synthetic",
        rights_ref="owned-workload-portfolio",
        max_usd="5",
        max_requests=500,
        independent_labels=True,
        representative=False,
        minimum=1,
        min_test_samples=3,
        automatic=True,
        exact_cache_contract=contract,
    )

    class LostPreparation:
        endpoint = control.endpoint

        def __call__(self, method, path, body=None):
            value = control(method, path, body)
            if method == "POST" and path.endswith("/experiments/plans"):
                raise ConnectionError("owned lost nonbillable portfolio setup reply")
            return value

    with pytest.raises(ConnectionError):
        prepare(root, LostPreparation(), tenant[0], **parameters)
    from cutback.agent_preparation import resume

    setup_id = load_session(root)["preparation_intent"]["requests"]["plan"]
    ready = resume(root, control)
    assert ready["phase"] == "PLAN_READY" and ready["plan"]["id"] == setup_id
    content = control(
        "POST",
        f"/v1/projects/{tenant[0]}/experiments/plans/{ready['plan']['id']}/content",
        {"purpose": "owned-portfolio-check"},
    )["specification"]
    assert (
        content["workload_portfolio"]["assessment"]["assessment_sha256"]
        == assessment["selected_assessment"]["assessment_sha256"]
    )
    assert any(value.get("exact_cache") for value in content["candidates"])
    assert any(value["model"] != content["baseline"]["model"] for value in content["candidates"])
    native_calls = []

    def transport(request):
        native_calls.append(1)
        body = json.loads(native_response(request).content)
        body.update(parallel_tool_calls=False, tool_choice="none", tools=[])
        return httpx.Response(200, json=body)

    executor = OpenAIExecutor(
        http_client=httpx.Client(transport=httpx.MockTransport(transport), trust_env=False)
    )
    executor.synthetic_transport = True

    def execute(planned):
        queued = advance(
            root, control, plan_hash=planned["plan"]["plan_hash"], approval_ref="owned-synthetic"
        )
        status = run_one(
            api[2].state.sessions,
            api[2].state.artifact_store,
            executor=executor,
            execution_enabled=True,
            only_run_id=UUID(queued["run_id"]),
        )
        assert status["status"] == "COMPLETED", status
        return advance(root, control)

    try:
        validated = execute(ready)
        cached = next(row for row in validated["decisions"] if row["candidate"].get("exact_cache"))
        assert cached["quality_gate_passed"] and cached["cheaper_on_sample"]
        assert cached["exact_cache"]["cache_hits"] > 0
        selected = cached["candidate"]["key"]

        class LostConfirmation:
            endpoint = control.endpoint
            writes = 0

            def __call__(self, method, path, body=None):
                value = control(method, path, body)
                if method == "POST" and path.endswith("/experiments/plans"):
                    self.writes += 1
                    if self.writes == 1:
                        raise ConnectionError("owned lost confirmation reply")
                return value

        lost = LostConfirmation()
        with pytest.raises(ValueError, match="CONFIRMATION_EXCEEDS_BUDGET"):
            confirm(
                root,
                control,
                candidate=selected,
                max_usd="5",
                max_requests=100,
                purpose="owned-confirmation",
            )
        assert load_session(root)["phase"] == "COMPLETED" and not load_session(root).get(
            "confirmation"
        )
        with pytest.raises(ConnectionError):
            confirm(
                root,
                lost,
                candidate=selected,
                max_usd="5",
                max_requests=200,
                purpose="owned-confirmation",
            )
        assert load_session(root)["phase"] == "PREPARING_CONFIRMATION"
        confirmed = confirm(
            root,
            lost,
            candidate=selected,
            max_usd="5",
            max_requests=200,
            purpose="owned-confirmation",
        )
        assert lost.writes == 2
        completed = execute(confirmed)
        assert gate(root, control, candidate=selected, sample_only=True)["passed"]
        report = control("GET", f"/v1/projects/{tenant[0]}/experiments/{completed['run_id']}")[
            "results"
        ]
        economic = report["workload_portfolio"]
        assert economic["trial_family_billing_complete"]
        assert int(economic["modeled_source_windows_to_cover_trial"]) > 0
        assert economic["full_task_cost_usd"] is None and economic["realized_savings_usd"] is None
        artifact = proposal(root, control, candidate=selected, purpose="owned-cache-source")
        assert artifact["diff"] and artifact["rollback_diff"]
        assert artifact["synthetic_execution"]["real_model_quality_verified"] is False
        assert not gate(root, control, candidate=selected)["passed"]
        assert (root / "app.py").read_bytes() == original
        from support_exact_cache import qualify_source_runtime

        qualify_source_runtime(
            api,
            tenant,
            root,
            artifact,
            load_session(root),
            contract,
            root.parent / "workload-source-review",
            monkeypatch,
            expected_model="gpt-4.1",
            expected_output_cap=64,
        )
        if os.getenv("CUTBACK_TEST_BROWSER") == "true":
            from pathlib import Path

            from support_browser import browser_command

            admin, viewer = (headers["Authorization"][7:] for headers in (api[1], tenant[2]))
            web = Path(__file__).resolve().parents[2] / "web"
            with owned_collector(api[2]) as endpoint:
                browser = subprocess.run(
                    browser_command(web, "workload-portfolio.spec.ts"),
                    cwd=web,
                    env={
                        **os.environ,
                        "CUTBACK_UI_URL": endpoint,
                        "CUTBACK_UI_PROJECT": tenant[0],
                        "CUTBACK_UI_KEY": admin,
                        "CUTBACK_PORTFOLIO_VIEWER": viewer,
                        "CUTBACK_WORKLOAD_PORTFOLIO_BROWSER": "true",
                        "CUTBACK_DEMO_VIDEO": "false",
                        "CUTBACK_UI_OUTPUT_DIR": str(root.parent / "portfolio-browser"),
                    },
                    capture_output=True,
                    text=True,
                    timeout=150,
                )
                safe = (
                    (browser.stdout + browser.stderr)
                    .replace(admin, "<credential-omitted>")
                    .replace(viewer, "<credential-omitted>")
                )
                assert browser.returncode == 0, safe
    finally:
        executor.close()
    assert native_calls
    import copy

    from cutback.core.request_profiles import fingerprint
    from cutback.server.db import CapturePolicyRecord
    from cutback.server.native_profile_db import NativeProfileAnalysis

    path = f"/v1/projects/{tenant[0]}/experiments/plans"
    body = {"dataset_id": ready["dataset_id"], "specification": content}
    # A retained assessment must remain current at every authority boundary,
    # including after one native call has already incurred its modeled charge.
    clone = api[0].post(path, headers=api[1], json=body)
    assert clone.status_code == 201, clone.json()
    clone_id = clone.json()["id"]
    approval = {"plan_hash": clone.json()["plan_hash"], "approval_ref": "owned-current-window"}

    def reprice(changed):
        with api[2].state.sessions.begin() as session:
            event = session.query(StoredEvent).filter_by(project_id=UUID(tenant[0])).first()
            if changed:
                snapshot = event.price_snapshot_id
                event.price_snapshot_id = "owned-changed-price-snapshot"
                return snapshot
            event.price_snapshot_id = source_snapshot

    source_snapshot = reprice(True)
    response = api[0].post(f"{path}/{clone_id}/approve", headers=api[1], json=approval)
    assert response.status_code == 409
    assert response.json()["code"] == "WORKLOAD_PORTFOLIO_CURRENT_SOURCE_WINDOW_REQUIRED"
    reprice(False)
    response = api[0].post(f"{path}/{clone_id}/approve", headers=api[1], json=approval)
    assert response.status_code == 200
    reprice(True)
    runs_path = f"/v1/projects/{tenant[0]}/experiments"
    response = api[0].post(runs_path, headers=api[1], json={"plan_id": clone_id})
    assert response.status_code == 409
    assert response.json()["code"] == "WORKLOAD_PORTFOLIO_CURRENT_SOURCE_WINDOW_REQUIRED"
    reprice(False)
    response = api[0].post(runs_path, headers=api[1], json={"plan_id": clone_id})
    assert response.status_code == 202, response.json()
    revoked_run = response.json()["id"]
    revoked_calls = []

    def revoked_transport(request):
        revoked_calls.append(1)
        if len(revoked_calls) == 1:
            reprice(True)
        return transport(request)

    revoked_executor = OpenAIExecutor(
        http_client=httpx.Client(transport=httpx.MockTransport(revoked_transport), trust_env=False)
    )
    revoked_executor.synthetic_transport = True
    try:
        failed = run_one(
            api[2].state.sessions,
            api[2].state.artifact_store,
            executor=revoked_executor,
            execution_enabled=True,
            only_run_id=UUID(revoked_run),
        )
        assert failed["status"] == "FAILED" and len(revoked_calls) == 1
        repeated = run_one(
            api[2].state.sessions,
            api[2].state.artifact_store,
            executor=revoked_executor,
            execution_enabled=True,
            only_run_id=UUID(revoked_run),
        )
        assert repeated["status"] == "IDLE" and len(revoked_calls) == 1
        from cutback.server.db import ExperimentRunRecord

        with api[2].state.sessions() as session:
            assert session.get(ExperimentRunRecord, UUID(revoked_run)).requests_used == 1
    finally:
        revoked_executor.close()
        reprice(False)
    foreign = copy.deepcopy(body)
    forged = foreign["specification"]["workload_portfolio"]["assessment"]
    forged["project_id"] = "00000000-0000-4000-8000-000000000001"
    forged["assessment_sha256"] = fingerprint(
        {key: value for key, value in forged.items() if key != "assessment_sha256"}
    )
    response = api[0].post(path, headers=api[1], json=foreign)
    assert (
        response.status_code == 409
        and response.json()["code"] == "WORKLOAD_PORTFOLIO_CURRENT_SOURCE_WINDOW_REQUIRED"
    )
    with api[2].state.sessions.begin() as session:
        event = session.query(StoredEvent).filter_by(project_id=UUID(tenant[0])).first()
        old_snapshot = event.price_snapshot_id
        event.price_snapshot_id = "owned-changed-price-snapshot"
    response = api[0].post(path, headers=api[1], json=body)
    assert (
        response.status_code == 409
        and response.json()["code"] == "WORKLOAD_PORTFOLIO_CURRENT_SOURCE_WINDOW_REQUIRED"
    )
    with api[2].state.sessions.begin() as session:
        event = session.query(StoredEvent).filter_by(project_id=UUID(tenant[0])).first()
        event.price_snapshot_id = old_snapshot
        analysis = session.get(
            NativeProfileAnalysis, UUID(assessment["selected_assessment"]["analysis_id"])
        )
        session.get(CapturePolicyRecord, analysis.policy_id).enabled = False
    response = api[0].post(path, headers=api[1], json=body)
    assert (
        response.status_code == 409
        and response.json()["code"] == "WORKLOAD_PORTFOLIO_CURRENT_SOURCE_WINDOW_REQUIRED"
    )
