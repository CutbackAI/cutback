"""One common-program approval can choose cache using real encrypted hit receipts."""

import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from integration.test_source_policy_program_api import prepared_program
from integration.test_source_policy_program_workflow import drive
from sqlalchemy import select
from support_agent import native_response
from support_native_agent import native_executor
from support_policy_application import application

import cutback
from cutback.agent_control import Control
from cutback.agent_policy_handoff import finish
from cutback.agent_policy_program import advance, prepare
from cutback.core.workspace import read_private
from cutback.policy_source import inspect_configuration
from cutback.server.db import ExperimentRunRecord
from cutback.server.execution_db import ExecutionCall

pytestmark = pytest.mark.postgres


def test_common_program_selects_exact_cache_with_known_costs_and_keeps_runtime_activation_separate(
    api, tenant, tmp_path, monkeypatch
):
    root = application(tmp_path)
    _, binding = inspect_configuration(root, "app.py")
    base, store, _, policy_id, executor, calls, _, _, request = prepared_program(
        api, tenant, tmp_path, monkeypatch, source_binding=binding
    )
    executor.close()
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        value = native_response(request).json()
        value.update(parallel_tool_calls=False, tool_choice="none", tools=[])
        return httpx.Response(200, json=value)

    executor = native_executor("responses.create", monkeypatch, respond)
    executor.synthetic_transport = True
    try:
        cases = [
            {
                "id": "owned-repeat-" + str(index),
                "group": "owned-family-" + chr(65 + index // 4),
                "input": "Owned repeated source input " + chr(65 + index // 4),
                "gold": "A",
            }
            for index in range(80)
        ]
        response = api[0].post(
            base + "/datasets",
            headers=api[1],
            json={
                "workflow": binding.workflow,
                "policy_id": policy_id,
                "source_type": "synthetic_fixture",
                "provenance_ref": "owned-cache-program-corpus",
                "independent_labels": True,
                "cases": cases,
            },
        )
        assert response.status_code == 201, response.text
        control = Control("http://127.0.0.1", api[1]["Authorization"][7:], client=api[0])
        row = prepare(
            root,
            control,
            tenant[0],
            dataset_id=response.json()["id"],
            baseline_snapshot_id=request["baseline_snapshot_id"],
            producer_file="app.py",
            purpose="owned-cache-program",
            max_usd="100",
            max_requests=1000,
            savings_per_success="0.01",
            forecast_successful_tasks=100000,
            minimum=1,
            min_test_samples=3,
            min_test_groups=3,
            generate=False,
            fixture_transport=True,
            limit=1,
            cache_contract={
                "freshness_ref": "owned-cache-program",
                "ttl_seconds": 60,
                "content_retention_authorized": True,
                "immutable_or_safely_invalidated": True,
                "operation_cost_upper_usd": "0.000001",
                "operation_cost_basis_ref": "owned-fixture",
            },
        )
        assert row["status"] == "READY_FOR_APPROVAL", row["economic_envelope"]
        assert row["economic_envelope"]["selected_control_keys"] == ["source-exact-cache"]
        advance(root, control, program_hash=row["program_hash"], approval_ref="owned-cache-budget")
        path = base + "/source-policy-programs/" + row["id"]
        completed = drive(api, store, executor, row, path)
        assert completed["status"] == "COMPLETED", completed["summary"]
        assert completed["summary"].get("sample_only_gate", {}).get("passed") is True, completed[
            "summary"
        ]
        assert [value["phase"] for value in completed["runs"]] == [
            "static_validation",
            "confirmation",
        ]
        assert len(calls) < completed["requests"] == completed["known_cost_requests"]
        with api[2].state.sessions() as session:
            run = session.get(
                ExperimentRunRecord, UUID(completed["summary"]["confirmation_run_id"])
            )
            candidate = next(
                value
                for value in run.results["models"]
                if value["model"]["key"] == "source-exact-cache"
            )
            cache = candidate["exact_cache"]
            assert cache["cache_hits"] > 0 and cache["execution_qualified"] is True
            assert cache["historical_cached_usage_priced_again"] is False
            assert (
                cache["production_hit_rate"] is None and cache["invoice_verified_cost_usd"] is None
            )
            receipts = session.scalars(
                select(ExecutionCall).where(ExecutionCall.run_id == run.id)
            ).all()
            assert any(
                value.cache_observation and value.cache_observation["origin"] == "encrypted_cache"
                for value in receipts
            )
        proposal = api[0].post(
            path + "/source-proposal", headers=api[1], json={"purpose": "owned-cache-review"}
        )
        assert proposal.status_code == 200, proposal.text
        assert proposal.json()["proposed"]["cache_execution"] is not None
        assert proposal.json()["decision"] == "synthetic_local_review_only"
        assert proposal.json()["full_task_cost_known"] is False
        before = len(calls)
        destination = tmp_path / "private-cache-handoff"
        handoff = finish(
            root,
            control,
            destination=destination,
            purpose="owned-cache-source-review",
            commands=[
                {
                    "key": "native-policy-contract",
                    "arguments": [
                        "-c",
                        "import sys, unittest; sys.path.insert(0, "
                        + repr(str(Path(cutback.__file__).resolve().parents[1]))
                        + "); unittest.main(module='test_app')",
                    ],
                    "timeout_seconds": 30,
                }
            ],
            coverage={binding.workflow: ["native-policy-contract"]},
            execute_local=True,
        )
        assert handoff["status"] == "REVIEWED", handoff
        review = read_private(destination / "review.json")
        assert review["status"] == "CACHE_RUNTIME_CONFIGURATION_REQUIRED"
        assert (
            review["cache_runtime_required"] is True and review["cache_runtime_verified"] is False
        )
        assert read_private(destination / "proposal.json")["cache_enabled_by_patch"] is False
        assert len(calls) == before
    finally:
        executor.close()
