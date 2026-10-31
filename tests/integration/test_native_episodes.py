import json
from decimal import Decimal
from uuid import UUID

import httpx
import httpx2
import pytest
from sqlalchemy import select
from support_evaluation import authorized_dataset, dataset_body, plan_for
from support_native_agent import native_executor, response_body

from cutback.server.db import CapturePolicyRecord, DatasetRecord, ExperimentRunRecord, JobRecord
from cutback.server.execution_db import ExecutionCall, ExecutionEpisode
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


def prepared_episode(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    surface="responses.create",
    *,
    transport_contract="native_provider",
    semantic=False,
    reactive=False,
    max_turns=3,
):
    client, admin, app = api
    base, store, initial, _ = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    with app.state.sessions() as session:
        policy_id = session.get(DatasetRecord, UUID(initial["id"])).provenance["policy_id"]
    data = dataset_body(6).model_dump(mode="json")
    data["policy_id"] = policy_id
    data["cases"] = [
        {
            "id": "episode-" + letter,
            "input": "Owned synthetic conversation " + letter,
            "episode": {
                "tools": [
                    {
                        "key": "lookup_ticket",
                        "description": "Read synthetic ticket",
                        "parameters": {
                            "type": "object",
                            "properties": {"ticket": {"type": "string"}},
                            "required": ["ticket"],
                            "additionalProperties": False,
                        },
                        "fixtures": [
                            {
                                "arguments": {"ticket": "synthetic-A"},
                                "result": {"priority": "urgent"},
                            }
                        ],
                    }
                ],
                "max_turns": max_turns,
                "assertions": {"required_tools": ["lookup_ticket"], "accepted_answers": ["Urgent"]},
            },
        }
        for letter in "GHIJKL"
    ]
    response = client.post(base + "/datasets", headers=admin, json=data)
    assert response.status_code == 201, response.text
    dataset = response.json()
    provider = "anthropic" if surface == "messages.create" else "openai"
    model = "claude-haiku-4-5" if provider == "anthropic" else "gpt-4.1-mini"
    with app.state.sessions.begin() as session:
        policy = session.get(CapturePolicyRecord, UUID(policy_id))
        policy.policy = {**policy.policy, "allowed_providers": ["openai", "anthropic", "local"]}
    spec = plan_for(
        dataset,
        baseline={"key": "baseline", "provider": provider, "model": model},
        candidates=[{"key": "candidate", "provider": provider, "model": model}],
        sample_limit=1,
        split="validation",
        evaluator_version="deterministic-v4",
        metric={"kind": "episode_contract", "minimum": 1},
        approved_providers=[provider],
        episode_execution={
            "openai_surface": "responses.create" if provider == "anthropic" else surface,
            "max_turns": max_turns,
            "transport": transport_contract,
        },
        retain_case_outputs=True,
    )
    if semantic:
        from support_episode_judge import with_semantic_judge

        spec = with_semantic_judge(spec)
    if reactive:
        from support_reactive_users import reactive_plan

        raw = reactive_plan().model_dump(mode="json")
        raw.update(
            dataset_hash=spec.dataset_hash,
            sample_limit=spec.sample_limit,
            split=spec.split,
            workflow=spec.workflow,
            retain_case_outputs=True,
            baseline=spec.baseline.model_dump(mode="json"),
            candidates=[m.model_dump(mode="json") for m in spec.candidates],
            approved_providers=sorted({*spec.approved_providers, "openai"}),
        )
        raw["episode_execution"]["openai_surface"] = spec.episode_execution.openai_surface
        raw["episode_execution"]["max_turns"] = max_turns
        spec = type(spec).model_validate(raw)
    response = client.post(
        base + "/experiments/plans",
        headers=admin,
        json={"dataset_id": dataset["id"], "specification": spec.model_dump(mode="json")},
    )
    assert response.status_code == 201, response.text
    plan = response.json()
    assert plan["requests"] == (2 * (max_turns + int(semantic) + int(reactive)))
    response = client.post(
        base + "/experiments/plans/" + plan["id"] + "/approve",
        headers=admin,
        json={"plan_hash": plan["plan_hash"], "approval_ref": "owned-synthetic-transport"},
    )
    assert response.status_code == 200, response.text
    response = client.post(base + "/experiments", headers=admin, json={"plan_id": plan["id"]})
    assert response.status_code == 202, response.text
    return base, store, UUID(response.json()["id"]), policy_id


@pytest.mark.parametrize(
    "surface", ["responses.create", "chat.completions.create", "messages.create"]
)
@pytest.mark.parametrize("mode", ["success", "outage", "unknown_usage", "revoked", "cancelled"])
def test_native_episode_worker_preserves_each_call_scope_charge_and_content_boundary(
    api, tenant, tmp_path, monkeypatch, surface, mode
):
    base, store, run_id, policy_id = prepared_episode(api, tenant, tmp_path, monkeypatch, surface)
    client, admin, app = api
    transport = httpx2 if surface == "messages.create" else httpx
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        with app.state.sessions() as session:
            call = session.scalar(
                select(ExecutionCall)
                .where(ExecutionCall.run_id == run_id)
                .order_by(ExecutionCall.sequence.desc())
                .limit(1)
            )
            assert call.state == "reserved" and call.spent_usd is None and call.reserved_usd > 0
            episode = session.get(ExecutionEpisode, call.episode_id)
            assert episode.run_id == run_id and episode.project_id == UUID(tenant[0])
            assert episode.state == "running" and episode.requests_used <= episode.max_requests
        if mode in {"revoked", "cancelled"}:
            with app.state.sessions.begin() as session:
                if mode == "revoked":
                    session.get(CapturePolicyRecord, UUID(policy_id)).enabled = False
                else:
                    session.get(ExperimentRunRecord, run_id).status = "CANCELLED"
                    session.scalar(
                        select(JobRecord).where(JobRecord.run_id == run_id)
                    ).status = "CANCELLED"
        if mode == "outage":
            return transport.Response(
                503,
                json={"error": {"type": "overloaded_error", "message": "PRIVATE_NATIVE_OUTAGE"}},
            )
        serialized = json.dumps(body)
        tooling = (
            "function_call_output" in serialized
            or '"role": "tool"' in serialized
            or "tool_result" in serialized
        )
        response = response_body(surface, "final" if tooling else "tool")
        if mode == "unknown_usage":
            response.pop("usage")
        return transport.Response(200, json=response)

    executor = native_executor(surface, monkeypatch, handle)
    result = run_one(
        app.state.sessions, store, executor=executor, execution_enabled=True, only_run_id=run_id
    )
    assert result["status"] == (
        "FAILED" if mode == "revoked" else "CANCELLED" if mode == "cancelled" else "COMPLETED"
    ), result
    with app.state.sessions() as session:
        receipts = session.scalars(
            select(ExecutionCall).where(ExecutionCall.run_id == run_id)
        ).all()
        run = session.get(ExperimentRunRecord, run_id)
        assert (
            len(receipts)
            == len(calls)
            == (4 if mode == "success" else 1 if mode in {"revoked", "cancelled"} else 2)
        )
        assert run.requests_used == len(calls)
        assert run.known_cost_requests == (0 if mode in {"outage", "unknown_usage"} else len(calls))
        assert run.spent_usd == sum((c.spent_usd or Decimal(0) for c in receipts), Decimal(0))
    report = client.get(base + "/experiments/" + str(run_id), headers=tenant[2]).json()
    assert "Owned synthetic conversation" not in json.dumps(
        report
    ) and "PRIVATE_NATIVE_OUTAGE" not in json.dumps(report)
    if mode == "success":
        assert all(row["quality"]["value"] == 1 for row in result["results"]["models"])
        content = client.post(
            base + f"/experiments/{run_id}/cases/paired",
            headers=admin,
            json={"purpose": "inspect-owned-episode"},
            params={"failed_only": False},
        )
        assert content.status_code == 200 and '"kind":"tool"' in content.text
    if mode in {"outage", "unknown_usage"}:
        assert all(
            row["quality"]["value"] is None and row["quality"]["failures"] == 0
            for row in result["results"]["models"]
        )
    assert (
        run_one(
            app.state.sessions, store, executor=executor, execution_enabled=True, only_run_id=run_id
        )["status"]
        == "IDLE"
    )
    executor.close()


def test_owned_worker_selects_qualified_agent_executor_with_intercepted_native_http(
    api, tenant, tmp_path, monkeypatch
):
    from cutback.evals import executors

    _, store, run_id, _ = prepared_episode(api, tenant, tmp_path, monkeypatch)
    calls = []

    def handle(request):
        calls.append(1)
        return httpx.Response(
            200,
            json=response_body(
                "responses.create",
                "final" if b"function_call_output" in request.content else "tool",
            ),
        )

    native = executors.OpenAIExecutor
    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "owned-synthetic-never-sent")
    monkeypatch.setattr(
        executors,
        "OpenAIExecutor",
        lambda: native(
            http_client=httpx.Client(transport=httpx.MockTransport(handle), trust_env=False)
        ),
    )
    result = run_one(api[2].state.sessions, store, execution_enabled=True, only_run_id=run_id)
    assert result["status"] == "COMPLETED", result
    assert len(calls) == result["results"]["requests"] == 4


def test_scripted_plan_cannot_run_on_ordinary_provider_key_worker(
    api, tenant, tmp_path, monkeypatch
):
    from cutback.evals import executors

    _, store, run_id, _ = prepared_episode(
        api, tenant, tmp_path, monkeypatch, transport_contract="scripted_test_only"
    )
    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "owned-synthetic-never-sent")

    def forbidden(*args, **kwargs):
        pytest.fail("Scripted-only plans must never construct a provider client")

    monkeypatch.setattr(executors, "OpenAIExecutor", forbidden)
    result = run_one(api[2].state.sessions, store, execution_enabled=True, only_run_id=run_id)
    assert result["status"] == "BLOCKED" and result["provider_calls"] == 0
    # Do not leave eligible work in the shared disposable test queue.
    cancelled = api[0].post(
        f"/v1/projects/{tenant[0]}/experiments/{run_id}/cancel", headers=api[1], json={}
    )
    assert cancelled.status_code == 200, cancelled.text
