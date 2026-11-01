"""One approved cached-current comparison, real PG/encrypted cache, and closed native SDK calls."""

import json
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from integration.test_source_policy_program_workflow import drive
from sqlalchemy import select
from support_agent import native_response
from support_native_agent import native_executor
from support_policy_cached_program import cached_program

from cutback.server.db import ExperimentPlanRecord, ExperimentRunRecord
from cutback.server.execution_db import ExecutionCall
from cutback.server.plan_artifacts import load_plan
from cutback.server.policy_program_db import SourcePolicyProgramRecord
from cutback.server.policy_program_worker import advance_one

pytestmark = pytest.mark.postgres


def execution(
    api, tenant, tmp_path, monkeypatch, *, requested=True, routing=False, uncertain=False
):
    base, store, _, _, previous, _, template, parent, _, _ = cached_program(
        api, tenant, tmp_path, monkeypatch, requested=requested
    )
    previous.close()
    calls = []

    def provider(request):
        body = json.loads(request.content)
        calls.append(body)
        if uncertain:
            raise httpx.ReadTimeout("OWNED_PRIVATE_CACHED_CURRENT_TIMEOUT", request=request)
        response = native_response(request).json()
        response.update(parallel_tool_calls=False, tool_choice="none", tools=[])
        response["output"][0]["content"][0]["text"] = (
            "B"
            if routing and body["model"] == "gpt-4.1-mini" and len(body["input"].encode()) > 100
            else "A"
        )
        return httpx.Response(200, json=response)

    executor = native_executor("responses.create", monkeypatch, provider)
    executor.synthetic_transport = True
    path = base + "/source-policy-programs/" + parent["id"]
    result = api[0].post(
        path + "/approve",
        headers=api[1],
        json={
            "program_hash": parent["program_hash"],
            "approval_ref": "owned-cached-current-budget",
        },
    )
    assert result.status_code == 200, result.text
    return store, executor, calls, template, parent, path


@pytest.mark.parametrize("requested", [False, True])
@pytest.mark.parametrize("routing", [False, True])
def test_approved_current_reference_is_preserved_and_training_uses_an_explicit_uncached_shadow(
    api, tenant, tmp_path, monkeypatch, requested, routing
):
    store, executor, calls, template, parent, path = execution(
        api, tenant, tmp_path, monkeypatch, requested=requested, routing=routing
    )
    try:
        result = drive(api, store, executor, parent, path)
        assert result["status"] == "COMPLETED", result["summary"]
        assert result["summary"]["code"] == "confirmation_completed", result["summary"]
        assert result["summary"]["sample_only_gate"]["passed"] is True
        assert ("training" in [row["phase"] for row in result["runs"]]) is routing
        assert sum(row["phase"] == "confirmation" for row in result["runs"]) == 1
        with api[2].state.sessions() as session:
            owner = session.get(SourcePolicyProgramRecord, UUID(parent["id"]))
            rows = session.scalars(
                select(ExecutionCall).where(
                    ExecutionCall.run_id.in_([UUID(row["id"]) for row in result["runs"]])
                )
            ).all()
            hits = sum(
                bool(row.cache_observation and row.cache_observation["origin"] == "encrypted_cache")
                for row in rows
            )
            assert (hits > 0) is requested
            assert len(rows) == owner.requests_used == owner.known_cost_requests
            assert len(calls) == len(rows) - hits
            assert sum((row.spent_usd for row in rows), Decimal(0)) == owner.spent_usd
            assert (
                owner.spent_usd
                <= owner.reserved_usd
                <= Decimal(owner.economic_envelope["worst_case_native_and_cache_usd"])
            )
            for link in result["runs"]:
                run = session.get(ExperimentRunRecord, UUID(link["id"]))
                plan = session.get(ExperimentPlanRecord, run.plan_id)
                frozen = load_plan(store, owner.project_id, plan.specification)
                binding = frozen.source_policy_execution
                assert binding.source == template.source_policy_execution.source
                assert (
                    binding.reference.policy_sha256
                    == template.source_policy_execution.reference.policy_sha256
                )
                assert binding.reference.cache_requested is (
                    False if link["phase"] == "training" else requested
                )
                if link["phase"] == "training":
                    assert binding.public_summary()["uncached_training_shadow"] is True
                    assert all(
                        row.cache_observation is None for row in rows if row.run_id == run.id
                    )
        before = len(calls)
        proposal = api[0].post(
            path + "/source-proposal",
            headers=api[1],
            json={"purpose": "owned-cached-current-review"},
        )
        assert proposal.status_code == 200, proposal.text
        assert proposal.json()["original"]["cache_execution"] is not None
        assert proposal.json()["full_task_cost_known"] is False
        assert len(calls) == before
    finally:
        executor.close()


def test_uncertain_cached_current_native_operation_is_never_automatically_replayed(
    api, tenant, tmp_path, monkeypatch
):
    store, executor, calls, _, parent, path = execution(
        api, tenant, tmp_path, monkeypatch, uncertain=True
    )
    try:
        result = drive(api, store, executor, parent, path)
        assert result["status"] in {"FAILED", "ABSTAINED"}
        assert len(calls) == 1 and result["requests"] == 1 and result["known_cost_requests"] == 0
        for _ in range(2):
            advance_one(
                api[2].state.sessions,
                store,
                execution_enabled=True,
                only_program_id=parent["id"],
                synthetic_transport=True,
            )
        assert len(calls) == 1
    finally:
        executor.close()
