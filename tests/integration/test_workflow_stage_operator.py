"""Owned PostgreSQL, real collector HTTP and pinned native parsers; no paid calls."""

import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from support_agent import native_response, prepare_fresh
from support_http import owned_collector as server
from typer.testing import CliRunner

from cutback.agent import advance, load_session, prepare
from cutback.agent_control import Control
from cutback.cli import app as cli
from cutback.server.db import Environment, ExperimentPlanRecord, ExperimentRunRecord, JobRecord
from cutback.workflow_operator import run_workflow

pytestmark = pytest.mark.postgres


def setup(api, tenant, tmp_path, monkeypatch, postgres_url, mode="native"):
    from cutback.evals import artifacts

    # The configured-worker credential gate is independent of the injected closed
    # native transport. A synthetic sentinel satisfies only this owned test gate.
    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "synthetic-only-never-provider")

    captured = {}
    original = artifacts.EncryptedFileStore

    def capture(path, key, **kwargs):
        captured.update(root=path, key=key)
        return original(path, key, **kwargs)

    monkeypatch.setattr(artifacts, "EncryptedFileStore", capture)
    if mode in {"native", "cache"}:
        from test_agent_exact_cache import cache_contract

        root, control, state, _ = prepare_fresh(
            api,
            tenant,
            tmp_path,
            monkeypatch,
            **(
                {
                    "search": True,
                    "fixture_transport": True,
                    "exact_cache_contract": cache_contract(),
                    "model": "gpt-4.1-mini",
                    "output_cap": 16,
                }
                if mode == "cache"
                else {}
            ),
        )

        def executor():
            from cutback.evals.executors import OpenAIExecutor

            def handle(request):
                calls.append(True)
                body = native_response(request).json()
                body.update(parallel_tool_calls=False, tool_choice="none", tools=[])
                return httpx.Response(200, json=body)

            value = OpenAIExecutor(
                http_client=httpx.Client(transport=httpx.MockTransport(handle), trust_env=False)
            )
            value.synthetic_transport = True
            return value

        if mode == "cache":
            monkeypatch.setenv("CUTBACK_CACHE_ENABLED", "true")
    else:
        if mode == "context":
            from support_compression_application import application

            from cutback.agent import analyze
            from cutback.demo_compression import synthetic_executor

            root = application(tmp_path)
            analyze(root)
            api[2].state.artifact_store = capture(tmp_path / "encrypted", b"w" * 32)
            control = Control("http://127.0.0.1", api[1]["Authorization"][7:], client=api[0])
            arguments = dict(
                files=["questions.jsonl"], joint_context=True, assumed_usd_per_cpu_hour="0.01"
            )

            def factory():
                return synthetic_executor(joint=True)
        elif mode == "retrieval":
            from support_retrieval_application import application, git

            from cutback.agent import analyze
            from cutback.demo_retrieval import synthetic_executor

            root = application(tmp_path)
            path = root / "app.py"
            path.write_text(path.read_text().replace('model="gpt-4.1-mini"', 'model="gpt-4.1"'))
            git(root, "add", ".")
            git(root, "commit", "-m", "Independent synthetic retrieval baseline")
            analyze(root)
            api[2].state.artifact_store = capture(tmp_path / "encrypted", b"w" * 32)
            control = Control("http://127.0.0.1", api[1]["Authorization"][7:], client=api[0])
            arguments = dict(
                files=["questions.jsonl"],
                automatic=True,
                retrieval_corpus_authorized=True,
                assumed_usd_per_cpu_hour="0.01",
            )
            factory = synthetic_executor
        else:
            from support_routing_application import application

            from cutback.agent import analyze
            from cutback.demo_routing import synthetic_executor

            root = application(tmp_path)
            analyze(root)
            api[2].state.artifact_store = capture(tmp_path / "encrypted", b"w" * 32)
            control = Control("http://127.0.0.1", api[1]["Authorization"][7:], client=api[0])
            arguments = dict(files=["tickets.jsonl"], learned_routing=True, fixture_transport=True)
            factory = synthetic_executor
        monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
        state = prepare(
            root,
            control,
            tenant[0],
            **(
                dict(
                    rights_basis="synthetic",
                    rights_ref="owned-operator-fixture",
                    max_usd="5",
                    max_requests=1000,
                    minimum=1,
                    independent_labels=True,
                    min_test_samples=3,
                )
                | arguments
            ),
        )

        def executor():
            value, requests = factory()
            calls.append(requests)
            return value

    monkeypatch.setattr(artifacts, "EncryptedFileStore", original)
    configuration = dict(
        endpoint=control.endpoint,
        allow_payloads=True,
        payload_key=base64.b64encode(captured["key"]).decode(),
    )
    monkeypatch.setattr(
        "cutback.local_stack.configured", lambda target: (captured["root"].parent, configuration)
    )
    monkeypatch.setattr("cutback.local_stack.database_url", lambda location, config: postgres_url)
    calls = []
    monkeypatch.setattr("cutback.evals.executors.ConfiguredExecutor", executor)
    return root, control, state, configuration, calls


@pytest.mark.parametrize("mode", ["native", "cache", "context", "retrieval", "routing"])
def test_actual_cli_completes_only_approved_stage_over_http(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    postgres_url,
    mode,
):
    root, _, prepared, configuration, calls = setup(
        api, tenant, tmp_path, monkeypatch, postgres_url, mode
    )
    source = (root / "app.py").read_bytes()
    with server(api[2]) as endpoint, httpx.Client(trust_env=False, timeout=30) as client:
        control = Control(endpoint, api[1]["Authorization"][7:], client=client)
        state = load_session(root)
        state["endpoint"] = endpoint
        from cutback.core.workspace import write_private

        write_private(root / ".cutback/agent/session.json", state)
        configuration["endpoint"] = endpoint
        monkeypatch.setattr("cutback.agent_control.connect", lambda target: (control, tenant[0]))
        pending = run_workflow(root, control, local_worker=True)
        assert pending["operator"]["code"] == "APPROVAL_REQUIRED" and not calls
        queued = advance(
            root,
            control,
            plan_hash=prepared["plan"]["plan_hash"],
            approval_ref="owned-synthetic-stage",
        )
        # This HTTP test collector shares the test process environment. Routing
        # prepares a new encrypted fit after completion and needs the collector's
        # existing content authorization to remain enabled, as in a separate service.
        previous_payload_flag = "true" if mode == "routing" else "false"
        monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", previous_payload_flag)
        result = CliRunner().invoke(
            cli, ["workflow", "run", "--target", str(root), "--local-worker"]
        )
        assert result.exit_code == 0, result.output
        receipt = json.loads(result.stdout)
        assert receipt["operator"]["local_worker_attempts"] == 1
        assert receipt["operator"]["approval_performed"] is False
        assert receipt["operator"]["queue_performed"] is False
        assert receipt["operator"]["automatic_billable_retry"] is False
        assert receipt["operator"]["code"] == (
            "APPROVAL_REQUIRED" if mode == "routing" else "STAGE_FINISHED"
        )
        assert calls and control.token not in result.output
        assert "synthetic-only-never-provider" not in result.output
        assert "Owned synthetic AA topic" not in result.output
        assert all(
            set(json.loads(line))
            == {
                "event",
                "polls",
                "local_worker_attempts",
                "active",
                "approval_required",
                "stage_finished",
            }
            for line in result.stderr.splitlines()
        )
        import os

        assert os.environ["CUTBACK_ALLOW_PAYLOADS"] == previous_payload_flag
        with api[2].state.sessions() as session:
            rows = session.scalars(
                select(ExperimentRunRecord).where(ExperimentRunRecord.project_id == UUID(tenant[0]))
            ).all()
            assert len(rows) == 1 and str(rows[0].id) == queued["run_id"]
            assert rows[0].status == "COMPLETED" and rows[0].results["billing_complete"]
            if mode != "native":
                assert not rows[0].results.get("lineage", {}).get("fresh_test_claim")
        count = len(calls)
        again = run_workflow(root, control, local_worker=True)
        assert again["operator"]["local_worker_attempts"] == 0 and len(calls) == count
        assert (root / "app.py").read_bytes() == source
        if mode == "routing":
            assert again["workflow"]["routing"]["stage"] == "validation"
            assert again["workflow"]["plan"]["plan_hash"] != prepared["plan"]["plan_hash"]


def test_lost_queue_ack_reconciles_without_reposting_and_remote_mode_never_constructs_client(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    postgres_url,
):
    root, control, state, _, calls = setup(api, tenant, tmp_path, monkeypatch, postgres_url)
    posts = []

    class Lost:
        endpoint = control.endpoint

        def __call__(self, method, path, body=None):
            reply = control(method, path, body)
            if method == "POST":
                posts.append(path)
            if method == "POST" and path.endswith("/experiments"):
                raise ValueError("COLLECTOR_RESPONSE_UNCERTAIN_INSPECT_SAVED_STATE")
            return reply

    with pytest.raises(ValueError, match="COLLECTOR_RESPONSE_UNCERTAIN"):
        advance(root, Lost(), plan_hash=state["plan"]["plan_hash"], approval_ref="owned-lost-ack")
    assert load_session(root)["phase"] == "QUEUEING"
    monkeypatch.setattr("cutback.agent_control.connect", lambda target: (Lost(), tenant[0]))
    command = CliRunner().invoke(
        cli, ["workflow", "run", "--target", str(root), "--max-polls", "1"]
    )
    assert command.exit_code == 2, command.output
    result = json.loads(command.stdout)
    assert result["operator"]["code"] == "LOCAL_WAIT_BOUND_REACHED"
    assert result["workflow"]["phase"] == "QUEUED" and not calls
    assert len(posts) == 2  # One original approval and queue; no automatic repost.


def test_missing_local_worker_configuration_exits_once_with_machine_readable_stop(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    postgres_url,
):
    root, control, state, _, calls = setup(api, tenant, tmp_path, monkeypatch, postgres_url)
    advance(root, control, plan_hash=state["plan"]["plan_hash"], approval_ref="owned-keyless")
    attempts = []

    def unconfigured():
        attempts.append(True)
        raise ValueError("NO_TEST_WORKER_CONFIGURATION")

    monkeypatch.setattr("cutback.evals.executors.ConfiguredExecutor", unconfigured)
    monkeypatch.setattr("cutback.agent_control.connect", lambda target: (control, tenant[0]))
    command = CliRunner().invoke(cli, ["workflow", "run", "--target", str(root), "--local-worker"])
    assert command.exit_code == 1, command.output
    result = json.loads(command.stdout)
    assert result["operator"]["code"] == "LOCAL_WORKER_STOPPED_INSPECT_CANONICAL_RUN"
    assert result["operator"]["local_worker_attempts"] == 1 and attempts == [True] and not calls


def test_crashed_local_run_recovers_without_credentials_or_transport_and_never_retries(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    postgres_url,
):
    root, control, state, _, calls = setup(api, tenant, tmp_path, monkeypatch, postgres_url)
    queued = advance(
        root, control, plan_hash=state["plan"]["plan_hash"], approval_ref="owned-crash"
    )
    from cutback.evals.experiments import upper_cost
    from cutback.server.execution_db import ExecutionCall
    from cutback.server.execution_ledger import ExecutionLedger
    from cutback.server.plan_artifacts import load_plan

    sessions = api[2].state.sessions
    with sessions.begin() as session:
        run = session.get(ExperimentRunRecord, UUID(queued["run_id"]))
        job = session.scalar(select(JobRecord).where(JobRecord.run_id == run.id))
        job.status = run.status = "RUNNING"
        job.lease_until = datetime.now(UTC) + timedelta(seconds=60)
        plan_row = session.get(ExperimentPlanRecord, run.plan_id)
        plan = load_plan(api[2].state.artifact_store, run.project_id, plan_row.specification)
        environment = session.scalar(
            select(Environment.id).where(
                Environment.project_id == run.project_id, Environment.name == "staging"
            )
        )
        job_id, run_id = job.id, run.id
    ledger = ExecutionLedger(
        sessions, run_id, job_id, plan, environment, authorize=lambda session: None
    )
    call_id = ledger.reserve(upper_cost(plan.baseline), plan.baseline, "evaluation")
    with sessions.begin() as session:
        past = datetime.now(UTC) - timedelta(seconds=1)
        session.get(JobRecord, job_id).lease_until = past
        session.get(ExecutionCall, call_id).lease_until = past

    def forbidden():
        pytest.fail("NO_CREDENTIAL_CONSTRUCTION_FOR_EXPIRED_RUN")

    monkeypatch.setattr("cutback.evals.executors.ConfiguredExecutor", forbidden)
    result = run_workflow(root, control, local_worker=True)
    assert result["workflow"]["phase"] == "STOPPED" and not calls
    assert result["operator"]["local_worker_attempts"] == 1
    assert run_workflow(root, control, local_worker=True)["operator"]["local_worker_attempts"] == 0
    with sessions() as session:
        run = session.get(ExperimentRunRecord, run_id)
        call = session.get(ExecutionCall, call_id)
        assert run.requests_used == 1 and run.known_cost_requests == 0 and run.reserved_usd > 0
        assert call.state == "uncertain" and call.complete is False


def test_revoked_routing_content_permission_stops_then_resumes_same_nonbillable_fit(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    postgres_url,
):
    root, control, state, _, calls = setup(
        api, tenant, tmp_path, monkeypatch, postgres_url, "routing"
    )
    advance(root, control, plan_hash=state["plan"]["plan_hash"], approval_ref="owned-routing-only")
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "false")
    with pytest.raises(ValueError, match="PAYLOAD_STORAGE_DISABLED"):
        run_workflow(root, control, local_worker=True)
    interrupted = load_session(root)
    assert interrupted["phase"] == "PREPARING_ROUTING_FIT"
    request = interrupted["routing"]["fit_request"].copy()
    count = len(calls)
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    resumed = run_workflow(root, control, local_worker=True)
    assert resumed["operator"]["code"] == "APPROVAL_REQUIRED"
    assert resumed["operator"]["local_worker_attempts"] == 0
    assert resumed["workflow"]["routing"]["fit_request"] == request and len(calls) == count
    with api[2].state.sessions() as session:
        assert session.query(ExperimentRunRecord).filter_by(project_id=UUID(tenant[0])).count() == 1


def test_local_provider_outage_retains_uncertain_charge_and_no_automatic_replay(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    postgres_url,
):
    from test_campaign_workflow import native

    root, control, state, _, _ = setup(api, tenant, tmp_path, monkeypatch, postgres_url)
    advance(root, control, plan_hash=state["plan"]["plan_hash"], approval_ref="owned-outage")
    executor, requests = native(monkeypatch, fail=True)
    monkeypatch.setattr("cutback.evals.executors.ConfiguredExecutor", lambda: executor)
    result = run_workflow(root, control, local_worker=True)
    assert result["workflow"]["phase"] == "STOPPED" and len(requests) == 1
    assert result["workflow"]["report"]["requests"] == 1
    assert run_workflow(root, control, local_worker=True)["operator"]["local_worker_attempts"] == 0
    assert len(requests) == 1
    with api[2].state.sessions() as session:
        run = session.get(ExperimentRunRecord, UUID(result["workflow"]["run_id"]))
        assert run.requests_used == 1 and run.known_cost_requests == 0 and run.reserved_usd > 0


@pytest.mark.parametrize("mutation", ["foreign_project", "changed_hash"])
def test_local_database_identity_refuses_before_recovery_or_client_creation(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    postgres_url,
    mutation,
):
    from uuid import uuid4

    from cutback.core.workspace import write_private
    from cutback.workflow_operator import work_local

    root, control, state, _, _ = setup(api, tenant, tmp_path, monkeypatch, postgres_url)
    advance(root, control, plan_hash=state["plan"]["plan_hash"], approval_ref="owned-scope")
    state = load_session(root)
    if mutation == "foreign_project":
        state["project_id"] = str(uuid4())
    else:
        state["plan"]["plan_hash"] = "b" * 64
    write_private(root / ".cutback/agent/session.json", state)
    monkeypatch.setattr(
        "cutback.server.execution_ledger.recover_execution_leases",
        lambda *a, **k: pytest.fail("NO_RECOVERY_OUTSIDE_CANONICAL_SCOPE"),
    )
    monkeypatch.setattr(
        "cutback.evals.executors.ConfiguredExecutor",
        lambda: pytest.fail("NO_CLIENT_OUTSIDE_CANONICAL_SCOPE"),
    )
    with pytest.raises(
        ValueError, match="LOCAL_OPERATOR_(RUN_SCOPE_MISMATCH|CANONICAL_PLAN_HASH_CHANGED)"
    ):
        work_local(root)
