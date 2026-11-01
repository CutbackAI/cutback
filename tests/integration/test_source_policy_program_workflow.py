"""Automatic whole-policy choices through one approval and closed native SDK transports."""

import json
from copy import deepcopy
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from integration.test_source_policy_program_api import prepared_program
from sqlalchemy import select
from support_agent import native_response
from support_native_agent import native_executor

from cutback.evals.campaigns import GENERATOR_INSTRUCTION
from cutback.evals.source_policy_programs import policy_confirmation_gate
from cutback.policy_program_operator import SourcePolicyProgramOperator
from cutback.server.db import ExperimentPlanRecord, ExperimentRunRecord
from cutback.server.execution_db import ExecutionCall
from cutback.server.plan_artifacts import load_plan
from cutback.server.policy_program_db import SourcePolicyProgramRecord, SourcePolicyProgramSelection
from cutback.server.policy_program_worker import advance_one

pytestmark = pytest.mark.postgres


def execution(api, tenant, tmp_path, monkeypatch, *, mode="routing", uncertain=False):
    changes = {}
    if mode == "generation":
        changes["generator"] = {
            "model": {
                "key": "generator",
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "max_input_tokens": 100000,
                "max_output_tokens": 1024,
            },
            "transport": "scripted_test_only",
            "rounds": 1,
            "variants_per_round": 1,
            "max_instruction_bytes": 1024,
            "max_output_tokens": 64,
        }
    base, store, _, _, previous, _, _, parent, _ = prepared_program(
        api, tenant, tmp_path, monkeypatch, **changes
    )
    previous.close()
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if uncertain:
            raise httpx.ReadTimeout("OWNED_PRIVATE_NATIVE_TIMEOUT", request=request)
        if body.get("instructions") == GENERATOR_INSTRUCTION:
            assert mode == "generation"
            document = json.loads(body["input"])
            assert document["training"] and "test-" not in str(document["training"])
            output = json.dumps(
                {
                    "proposals": [
                        {
                            "seed_key": "candidate",
                            "instruction": "Owned generated instruction.",
                            "max_output_tokens": 32,
                        }
                    ]
                }
            )
        elif (
            mode != "static"
            and body["model"] == "gpt-4.1-mini"
            and len(body["input"].encode()) > 100
            and body.get("instructions") != "Owned generated instruction."
        ):
            output = "B"
        else:
            output = "A"
        result = native_response(request).json()
        result["output"][0]["content"][0]["text"] = output
        return httpx.Response(200, json=result)

    executor = native_executor("responses.create", monkeypatch, handler)
    executor.synthetic_transport = True
    path = base + "/source-policy-programs/" + parent["id"]
    approved = api[0].post(
        path + "/approve",
        headers=api[1],
        json={
            "program_hash": parent["program_hash"],
            "approval_ref": "owned-program-fixture",
        },
    )
    assert approved.status_code == 200, approved.text
    return base, store, executor, calls, parent, path


def drive(api, store, executor, parent, path):
    project = path.split("/")[3]
    result = SourcePolicyProgramOperator(
        api[2].state.sessions,
        store,
        executor=executor,
        project_id=project,
        program_id=parent["id"],
        program_hash=parent["program_hash"],
    ).run(max_operations=10, max_wait_seconds=360, poll_seconds=0.1)
    assert result["status"] in {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED", "ABSTAINED"}, result
    response = api[0].get(path, headers=api[1])
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("mode", ["static", "routing", "generation"])
def test_one_approval_selects_one_policy_and_confirms_once_without_running_unneeded_stages(
    api, tenant, tmp_path, monkeypatch, mode
):
    _, store, executor, calls, parent, path = execution(
        api, tenant, tmp_path, monkeypatch, mode=mode
    )
    try:
        current = drive(api, store, executor, parent, path)
        assert current["status"] == "COMPLETED", current["summary"]
        assert current["summary"]["code"] == "confirmation_completed", current["summary"]
        assert current["summary"]["sample_only_gate"]["passed"] is True, current["summary"]
        assert current["summary"]["confirmation_gate"]["passed"] is False
        assert (
            current["summary"]["selected_strategy"]
            == {
                "static": "static",
                "routing": "learned",
                "generation": "generated",
            }[mode]
        )
        phases = [value["phase"] for value in current["runs"]]
        assert phases.count("confirmation") == 1 and phases[0] == "static_validation"
        assert ("training" in phases) == (mode == "routing")
        assert ("generation" in phases) == (mode == "generation")
        assert len(
            [value for value in calls if value.get("instructions") == GENERATOR_INSTRUCTION]
        ) == int(mode == "generation")
        assert current["summary"]["production_savings_usd"] is None
        assert current["summary"]["invoice_verified"] is False
        before_proposal = len(calls)
        proposed = api[0].post(
            path + "/source-proposal", headers=api[1], json={"purpose": "owned-source-review"}
        )
        assert proposed.status_code == 200, proposed.text
        assert proposed.json()["version"] == "cutback.source-policy-proposal.v1"
        assert proposed.json()["decision"] == "synthetic_local_review_only"
        assert proposed.json()["full_task_cost_known"] is False
        assert len(calls) == before_proposal
        with api[2].state.sessions() as session:
            row = session.get(SourcePolicyProgramRecord, UUID(parent["id"]))
            links = current["runs"]
            receipts = session.scalars(
                select(ExecutionCall).where(
                    ExecutionCall.run_id.in_([UUID(value["id"]) for value in links]),
                )
            ).all()
            assert len(receipts) == len(calls) == row.requests_used == row.known_cost_requests
            assert sum((value.spent_usd for value in receipts), Decimal(0)) == row.spent_usd
            assert (
                row.spent_usd
                <= row.reserved_usd
                <= Decimal(row.economic_envelope["worst_case_native_and_cache_usd"])
            )
            assert (
                len(
                    session.scalars(
                        select(SourcePolicyProgramSelection).where(
                            SourcePolicyProgramSelection.program_id == row.id,
                        )
                    ).all()
                )
                == 1
            )
            confirmation = session.get(
                ExperimentRunRecord, UUID(current["summary"]["confirmation_run_id"])
            )
            planned = session.get(ExperimentPlanRecord, confirmation.plan_id)
            frozen = load_plan(store, row.project_id, planned.specification)
            report = confirmation.results
            assert policy_confirmation_gate(report, frozen, sample_only=True)["passed"] is True
            for change in (
                "source",
                "dataset",
                "samples",
                "duplicate_model",
                "unpaired",
                "paired_delta",
            ):
                forged = deepcopy(report)
                if change == "source":
                    forged["source_policy_execution"]["source"]["workflow"] = "other-workflow"
                elif change == "dataset":
                    forged["dataset_hash"] = "0" * 64
                elif change == "samples":
                    forged["sample_n"] = True
                elif change == "duplicate_model":
                    forged["models"].append(deepcopy(forged["models"][0]))
                elif change == "unpaired":
                    forged["models"][0]["requests"] -= 1
                else:
                    forged["recommendations"][0]["paired_delta"]["sample_n"] -= 1
                assert policy_confirmation_gate(forged, frozen, sample_only=True)["passed"] is False
        exported = api[0].get(
            path.rsplit("/source-policy-programs/", 1)[0] + "/export", headers=api[1]
        )
        assert exported.status_code == 200
        assert all(
            table in exported.text
            for table in (
                "source_policy_programs",
                "source_policy_program_runs",
                "source_policy_program_selections",
                "source_policy_program_payloads",
            )
        )
        assert "OWNED_PRIVATE_POLICY_INSTRUCTION" not in exported.text
        assert "Owned generated instruction." not in exported.text
    finally:
        executor.close()


def test_uncertain_native_call_holds_the_whole_program_and_never_replays_on_later_ticks(
    api, tenant, tmp_path, monkeypatch
):
    _, store, executor, calls, parent, path = execution(
        api, tenant, tmp_path, monkeypatch, uncertain=True
    )
    try:
        current = drive(api, store, executor, parent, path)
        assert current["status"] == "FAILED" and len(calls) == 1
        assert current["requests"] == 1 and current["known_cost_requests"] == 0
        assert Decimal(current["reserved_usd"]) > 0 and current["billing_complete"] is False
        for _ in range(2):
            assert (
                advance_one(
                    api[2].state.sessions,
                    store,
                    execution_enabled=True,
                    only_program_id=parent["id"],
                    synthetic_transport=True,
                )["status"]
                == "IDLE"
            )
        assert len(calls) == 1
    finally:
        executor.close()
