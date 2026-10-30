"""Real PostgreSQL, native SDK and SQLite; no OS-isolation claim in this module."""

import hashlib
import json
import secrets
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from uuid import UUID, uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from support_evaluation import authorized_dataset
from support_isolated_tools import record_case, record_model_reply, scoped_broker
from support_native_agent import native_executor
from unit.test_guardrails import policy
from unit.test_isolated_episode_contract import isolated_plan
from unit.test_tool_broker import LocalTestRuntime

from cutback.core.guardrails import GuardrailBinding
from cutback.guardrail_client import GuardrailClient
from cutback.guardrail_service import GuardrailClientAuthority, create_guardrail_service
from cutback.server.db import ExperimentRunRecord, Project
from cutback.server.execution_db import ExecutionCall, ExecutionEpisode
from cutback.server.guardrail_db import GuardrailOperationRow, GuardrailPolicyRow
from cutback.server.migrate import CURRENT_REVISION
from cutback.server.tool_db import ToolOperationRecord
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


def prepare(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    *,
    deny_stage=None,
    mode="fail_closed",
    surface="responses.create",
):
    base, store, previous, _ = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    project, principal, broker, service = UUID(tenant[0]), uuid4(), uuid4(), uuid4()
    image = "sha256:" + "a" * 64
    capture_id = previous["provenance"]["policy_id"]
    if surface == "messages.create":
        response = api[0].post(
            base + "/capture-policies",
            headers=api[1],
            json={
                "enabled": True,
                "approval_ref": "owned-guarded-messages",
                "rights_basis": "synthetic",
                "environments": ["staging"],
                "workflows": ["classify"],
                "allowed_providers": ["anthropic"],
                "sample_rate": 1,
                "max_samples": 100,
                "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            },
        )
        assert response.status_code == 201, response.text
        capture_id = response.json()["id"]
    definition = policy(
        project_id=project,
        principal_id=principal,
        workflows=["classify"],
        protected_stages=["tool_input", "tool_output"],
        on_unavailable=mode,
        max_evaluation_ms=1000,
        rules=[
            {
                "kind": "literal_deny",
                "key": "owned-private-rule-" + stage,
                "stages": [stage],
                "terms": ["resolved" if stage == deny_stage else "OWNED_UNMATCHED_CANARY"],
                "normalization": "nfkc_casefold",
            }
            for stage in ("tool_input", "tool_output")
        ],
    )
    response = api[0].post(
        base + "/runtime-guardrails",
        headers=api[1],
        json={
            "service_id": str(service),
            "capture_policy_id": capture_id,
            "policy": definition.model_dump(mode="json"),
            "max_checks": 100,
        },
    )
    assert response.status_code == 201, response.text
    registered = response.json()
    response = api[0].post(
        base + "/runtime-guardrails/" + registered["id"] + "/approve",
        headers=api[1],
        json={
            "approval_hash": registered["approval_hash"],
            "approval_ref": str(uuid4()),
            "expected_state_version": registered["state_version"],
        },
    )
    assert response.status_code == 200, response.text
    binding = GuardrailBinding.model_validate(response.json()["binding"])
    response = api[0].post(
        base + "/datasets",
        headers=api[1],
        json={
            "workflow": "classify",
            "policy_id": capture_id,
            "source_type": "synthetic_fixture",
            "independent_labels": False,
            "provenance_ref": "owned-guarded-sql-controls",
            "cases": [record_case(project, principal) for _ in range(20)],
        },
    )
    assert response.status_code == 201, response.text
    dataset = response.json()
    raw = isolated_plan(project, principal, broker, image, sample_limit=1).model_dump(mode="json")
    raw["dataset_hash"] = dataset["immutable_hash"]
    provider = "anthropic" if surface == "messages.create" else "openai"
    for target in [raw["baseline"], *raw["candidates"]]:
        target.update(
            provider=provider,
            model="claude-haiku-4-5" if provider == "anthropic" else "gpt-4.1-mini",
        )
    raw["approved_providers"] = [provider]
    if provider == "openai":
        raw["episode_execution"]["openai_surface"] = surface
    raw["evaluator_version"] = "deterministic-v10"
    raw["episode_execution"]["version"] = "native-isolated-record-guarded-v1"
    raw["episode_execution"]["isolated_tools"].update(
        version="isolated-record-guarded-tools-v1",
        runtime_guardrails=binding.model_dump(mode="json"),
    )
    response = api[0].post(
        base + "/experiments/plans",
        headers=api[1],
        json={"dataset_id": dataset["id"], "specification": raw},
    )
    assert response.status_code == 201, response.text
    plan = response.json()
    response = api[0].post(
        base + "/experiments/plans/" + plan["id"] + "/approve",
        headers=api[1],
        json={"plan_hash": plan["plan_hash"], "approval_ref": "owned-guarded-native-test"},
    )
    assert response.status_code == 200, response.text
    response = api[0].post(base + "/experiments", headers=api[1], json={"plan_id": plan["id"]})
    assert response.status_code == 202, response.text
    return (
        store,
        UUID(response.json()["id"]),
        project,
        principal,
        broker,
        image,
        definition,
        binding,
    )


@contextmanager
def checker(definition, binding, *, outage=False):
    token = secrets.token_urlsafe(48)
    app = create_guardrail_service(
        service_id=binding.service_id,
        policies=[definition],
        clients=[
            GuardrailClientAuthority(
                project_id=binding.project_id,
                principal_id=binding.principal_id,
                token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                policy_ids=(binding.policy_id,),
                expires_at=binding.expires_at,
            )
        ],
    )
    with TestClient(app, base_url="https://owned-guard.invalid") as http:

        def dispatch(request):
            if outage:
                raise httpx.ReadError("OWNED_PRIVATE_OUTAGE")
            response = http.request(
                request.method,
                request.url.path,
                headers=dict(request.headers),
                content=request.content,
            )
            return httpx.Response(
                response.status_code, content=response.content, headers=dict(response.headers)
            )

        client = GuardrailClient(
            "https://owned-guard.invalid",
            token,
            binding=binding,
            workflow="classify",
            transport=httpx.MockTransport(dispatch),
        )
        try:
            yield client
        finally:
            client.close()


@pytest.mark.parametrize("deny_stage", [None, "tool_input", "tool_output"])
@pytest.mark.parametrize(
    "surface", ["responses.create", "chat.completions.create", "messages.create"]
)
def test_guard_decisions_bracket_real_broker_effects_without_erasing_writes(
    api, tenant, tmp_path, monkeypatch, deny_stage, api_database_url, surface
):
    store, run, project, principal, broker, image, definition, binding = prepare(
        api, tenant, tmp_path, monkeypatch, deny_stage=deny_stage, surface=surface
    )
    calls, sessions, broker_calls = [], api[2].state.sessions, []

    def model(request):
        calls.append(request)
        transport = __import__("httpx2" if surface == "messages.create" else "httpx")
        return transport.Response(200, json=record_model_reply(request, surface=surface))

    original = LocalTestRuntime.execute

    def execute(runtime, operation, tool, arguments):
        with sessions() as session:
            row = session.get(ToolOperationRecord, operation)
            decision = session.get(
                GuardrailOperationRow, (project, binding.policy_id, operation, "tool_input")
            )
            assert row.broker_intent == "STARTED" and row.result_delivery == "NOT_RELEASED"
            assert decision.decision["action"] == "allow"
        broker_calls.append(tool)
        return original(runtime, operation, tool, arguments)

    monkeypatch.setattr(LocalTestRuntime, "execute", execute)
    executor = native_executor(surface, monkeypatch, model)
    try:
        with (
            checker(definition, binding) as guard,
            scoped_broker(
                project,
                principal,
                broker,
                image,
                endpoint="npipe:////./pipe/synthetic-test-only",
                runtime_factory=LocalTestRuntime,
            ) as (client, _),
        ):
            result = run_one(
                sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=run,
                tool_client=client,
                guardrail_client=guard,
            )
        assert result["status"] == "COMPLETED", result
        assert len(calls) == (6 if deny_stage is None else 4)
        assert len(broker_calls) == (2 if deny_stage == "tool_input" else 4)
        with sessions() as session:
            operations = session.scalars(
                select(ToolOperationRecord).where(ToolOperationRecord.run_id == run)
            ).all()
            episodes = session.scalars(
                select(ExecutionEpisode).where(ExecutionEpisode.run_id == run)
            ).all()
            assert len(operations) == 4
            patches = [row for row in operations if row.tool_kind == "records_patch"]
            assert len(patches) == 2
            for row in patches:
                assert row.state_changed is (deny_stage != "tool_input")
                assert row.state == ("DENIED" if deny_stage == "tool_input" else "RETURNED")
                assert row.broker_intent == (
                    "NOT_STARTED" if deny_stage == "tool_input" else "STARTED"
                )
                assert row.result_delivery == ("AUTHORIZED" if deny_stage is None else "WITHHELD")
            assert all(row.summary["containment_confirmed"] for row in episodes)
            assert session.get(ExperimentRunRecord, run).known_cost_requests == len(calls)
            native = session.scalars(select(ExecutionCall).where(ExecutionCall.run_id == run)).all()
            assert len(native) == len(calls)
            public = json.dumps([row.summary for row in episodes])
            assert "OWNED_UNMATCHED_CANARY" not in public and "owned-private-rule" not in public
            patch_id, session_id = patches[0].id, patches[0].session_id
        base = f"/v1/projects/{project}"
        path = base + f"/experiments/{run}/tool-sessions/{session_id}/operations"
        response = api[0].get(path, headers=tenant[2])
        assert response.status_code == 200
        retained = next(row for row in response.json()["items"] if row["id"] == str(patch_id))
        assert retained["result_delivery"] == ("AUTHORIZED" if deny_stage is None else "WITHHELD")
        assert retained["guardrail_policy_id"] == str(binding.policy_id)
        assert "arguments" not in response.text and "OWNED_UNMATCHED_CANARY" not in response.text
        if deny_stage is None:
            # A mismatched guard identity cannot be attached to an existing
            # session, even by a caller bypassing HTTP validation.
            with pytest.raises(IntegrityError), sessions.begin() as session:
                session.get(ToolOperationRecord, patch_id).guardrail_policy_id = uuid4()
            cfg = Config()
            cfg.set_main_option(
                "script_location", str(files("cutback.server").joinpath("migrations"))
            )
            monkeypatch.setenv("CUTBACK_DATABASE_URL", api_database_url)
            command.check(cfg)
            with pytest.raises(RuntimeError, match="GUARDED_TOOL_DOWNGRADE_REQUIRES"):
                command.downgrade(cfg, "0025")
            assert (
                api[0].get("/v1/health/ready", headers=api[1]).json()["schema_revision"]
                == CURRENT_REVISION
            )
            exported = api[0].get(base + "/export", headers=api[1])
            assert exported.status_code == 200
            assert "OWNED_UNMATCHED_CANARY" not in exported.text
            with sessions() as session:
                slug = session.get(Project, project).slug
            assert (
                api[0].delete(base, headers=api[1], params={"confirm_slug": slug}).status_code
                == 200
            )
            command.downgrade(cfg, "0025")
            command.upgrade(cfg, "head")
            command.check(cfg)
    finally:
        executor.close()


@pytest.mark.parametrize("mode", ["fail_closed", "fail_open"])
def test_checker_outage_cannot_be_certified_as_complete_guard_coverage(
    api, tenant, tmp_path, monkeypatch, mode
):
    store, run, project, principal, broker, image, definition, binding = prepare(
        api, tenant, tmp_path, monkeypatch, mode=mode
    )
    calls, sessions = [], api[2].state.sessions

    def model(request):
        calls.append(request)
        return httpx.Response(200, json=record_model_reply(request))

    executor = native_executor("responses.create", monkeypatch, model)
    try:
        with (
            checker(definition, binding, outage=True) as guard,
            scoped_broker(
                project,
                principal,
                broker,
                image,
                endpoint="npipe:////./pipe/synthetic-test-only",
                runtime_factory=LocalTestRuntime,
            ) as (client, _),
        ):
            result = run_one(
                sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=run,
                tool_client=client,
                guardrail_client=guard,
            )
        assert result["status"] == "COMPLETED", result
        assert len(calls) == (6 if mode == "fail_open" else 2)
        with sessions() as session:
            episodes = session.scalars(
                select(ExecutionEpisode).where(ExecutionEpisode.run_id == run)
            ).all()
            assert len(episodes) == 2
            for row in episodes:
                assert row.summary["status"] == "unknown" and row.summary["score"] is None
                assert not row.summary["runtime_guardrails"]["coverage_complete"]
                assert row.summary["runtime_guardrails"]["unavailable"] > 0
            operations = session.scalars(
                select(ToolOperationRecord).where(ToolOperationRecord.run_id == run)
            ).all()
            assert all(
                row.result_delivery == ("AUTHORIZED" if mode == "fail_open" else "WITHHELD")
                for row in operations
            )
    finally:
        executor.close()


def test_lost_write_ack_is_unknown_and_never_replayed_by_guarded_worker(
    api, tenant, tmp_path, monkeypatch
):
    store, run, project, principal, broker, image, definition, binding = prepare(
        api, tenant, tmp_path, monkeypatch
    )
    calls, writes, sessions = [], [], api[2].state.sessions
    original = LocalTestRuntime.execute

    def lose(runtime, operation, tool, arguments):
        result = original(runtime, operation, tool, arguments)
        if tool == "records_patch":
            assert result["state_changed"]
            writes.append(operation)
            raise ConnectionError("OWNED_PRIVATE_WRITE_ACK_LOST")
        return result

    monkeypatch.setattr(LocalTestRuntime, "execute", lose)

    def model(request):
        calls.append(request)
        return httpx.Response(200, json=record_model_reply(request))

    executor = native_executor("responses.create", monkeypatch, model)
    try:
        with (
            checker(definition, binding) as guard,
            scoped_broker(
                project,
                principal,
                broker,
                image,
                endpoint="npipe:////./pipe/synthetic-test-only",
                runtime_factory=LocalTestRuntime,
            ) as (client, _),
        ):
            result = run_one(
                sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=run,
                tool_client=client,
                guardrail_client=guard,
            )
            assert result["status"] == "FAILED", result
            assert len(calls) == 2 and len(writes) == 1
            again = run_one(
                sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=run,
                tool_client=client,
                guardrail_client=guard,
            )
            assert again["status"] == "IDLE" and len(calls) == 2 and len(writes) == 1
        with sessions() as session:
            row = session.get(ToolOperationRecord, writes[0])
            assert row.state == "UNCERTAIN" and row.state_changed is None
            assert row.broker_intent == row.result_delivery == "UNKNOWN"
            assert session.get(ExperimentRunRecord, run).known_cost_requests == 2
    finally:
        executor.close()


def test_current_policy_revocation_after_write_withholds_reply_and_preserves_known_effect(
    api, tenant, tmp_path, monkeypatch
):
    store, run, project, principal, broker, image, definition, binding = prepare(
        api, tenant, tmp_path, monkeypatch
    )
    sessions, calls, writes = api[2].state.sessions, [], []
    original = LocalTestRuntime.execute

    def revoke_after_write(runtime, operation, tool, arguments):
        result = original(runtime, operation, tool, arguments)
        if tool == "records_patch":
            assert result["state_changed"]
            writes.append(operation)
            with sessions() as session:
                row = session.get(GuardrailPolicyRow, binding.policy_id)
                body = {
                    "approval_hash": row.approval_hash,
                    "expected_state_version": row.state_version,
                }
            disabled = api[0].post(
                f"/v1/projects/{project}/runtime-guardrails/{binding.policy_id}/disable",
                headers=api[1],
                json=body,
            )
            assert disabled.status_code == 200
        return result

    monkeypatch.setattr(LocalTestRuntime, "execute", revoke_after_write)

    def model(request):
        calls.append(request)
        return httpx.Response(200, json=record_model_reply(request))

    executor = native_executor("responses.create", monkeypatch, model)
    try:
        with (
            checker(definition, binding) as guard,
            scoped_broker(
                project,
                principal,
                broker,
                image,
                endpoint="npipe:////./pipe/synthetic-test-only",
                runtime_factory=LocalTestRuntime,
            ) as (client, _),
        ):
            result = run_one(
                sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=run,
                tool_client=client,
                guardrail_client=guard,
            )
        assert result["status"] == "FAILED", result
        assert len(calls) == 2 and len(writes) == 1
        with sessions() as session:
            row = session.get(ToolOperationRecord, writes[0])
            assert row.state == "RETURNED" and row.state_changed is True
            assert row.broker_intent == "STARTED" and row.result_delivery == "WITHHELD"
            assert session.get(ExperimentRunRecord, run).known_cost_requests == 2
    finally:
        executor.close()
