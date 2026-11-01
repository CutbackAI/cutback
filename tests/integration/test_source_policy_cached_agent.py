"""Automatic preparation, one approval, bounded native search and checked source handoff."""

import json
from pathlib import Path

import httpx
import pytest
from integration.test_source_policy_program_workflow import drive
from support_agent import native_response
from support_native_agent import native_executor
from support_policy_application import cached_application
from support_policy_cached_program import cached_program

import cutback
from cutback.agent_control import Control
from cutback.agent_policy_handoff import finish
from cutback.agent_policy_program import advance, prepare
from cutback.core.workspace import read_private
from cutback.evals.campaigns import GENERATOR_INSTRUCTION
from cutback.policy_source import inspect_configuration
from cutback.scanner import scan

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("requested", [False, True])
@pytest.mark.parametrize("mode", ["static", "generation", "routing"])
def test_agent_preserves_configured_cache_and_observed_activation_through_search_and_review(
    api, tenant, tmp_path, monkeypatch, requested, mode
):
    root, current = cached_application(tmp_path)
    _, binding = inspect_configuration(root, "app.py")
    site = next(row for row in scan(root)["call_sites"] if row["source_file"] == "app.py")
    base, store, dataset, _, previous, _, _, _, request, _ = cached_program(
        api,
        tenant,
        tmp_path,
        monkeypatch,
        requested=requested,
        source_binding=binding,
        cache_execution=current.cache_execution.model_dump(mode="json"),
        cache_controls=True,
        call_site_id=site["call_site_id"],
    )
    previous.close()
    calls, documents = [], []

    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        if body.get("instructions") == GENERATOR_INSTRUCTION:
            assert mode == "generation"
            document = json.loads(body["input"])
            documents.append(document)
            assert document["training"] and "test-" not in str(document["training"])
            seed = next(
                value["key"] for value in document["seeds"] if value["model"] == "gpt-4.1-mini"
            )
            output = json.dumps(
                {
                    "proposals": [
                        {
                            "seed_key": seed,
                            "instruction": "Owned cached-current generated instruction.",
                            "max_output_tokens": 32,
                        }
                    ]
                }
            )
        else:
            output = (
                "B"
                if mode != "static"
                and body["model"] == "gpt-4.1-mini"
                and len(body["input"].encode()) > 100
                and body.get("instructions") != "Owned cached-current generated instruction."
                else "A"
            )
        value = native_response(request).json()
        value.update(parallel_tool_calls=False, tool_choice="none", tools=[])
        value["output"][0]["content"][0]["text"] = output
        return httpx.Response(200, json=value)

    executor = native_executor("responses.create", monkeypatch, respond)
    executor.synthetic_transport = True
    control = Control("http://127.0.0.1", api[1]["Authorization"][7:], client=api[0])
    try:
        row = prepare(
            root,
            control,
            tenant[0],
            dataset_id=dataset["id"],
            baseline_snapshot_id=request["baseline_snapshot_id"],
            producer_file="app.py",
            purpose="owned-cached-agent-review",
            max_usd="100",
            max_requests=2000,
            max_wall_seconds=600,
            savings_per_success="0.01",
            forecast_successful_tasks=100000,
            minimum=1,
            min_test_samples=3,
            min_test_groups=3,
            generate=mode == "generation",
            fixture_transport=True,
            limit=1,
        )
        assert calls == [] and row["status"] == "READY_FOR_APPROVAL"
        assert row["economic_envelope"]["reference_runtime_cache_requested"] is requested
        assert row["economic_envelope"]["reference_execution_scope"] == (
            "isolated_cold_cache_replay" if requested else "uncached_source_policy_replay"
        )
        assert (
            prepare(
                root,
                control,
                tenant[0],
                dataset_id=dataset["id"],
                baseline_snapshot_id=request["baseline_snapshot_id"],
                producer_file="app.py",
                purpose="owned-cached-agent-review",
                max_usd="100",
                max_requests=2000,
                max_wall_seconds=600,
                savings_per_success="0.01",
                forecast_successful_tasks=100000,
                minimum=1,
                min_test_samples=3,
                min_test_groups=3,
                generate=mode == "generation",
                fixture_transport=True,
                limit=1,
            )["program_hash"]
            == row["program_hash"]
        )
        advance(root, control, program_hash=row["program_hash"], approval_ref="owned-budget")
        path = base + "/source-policy-programs/" + row["id"]
        result = drive(api, store, executor, row, path)
        assert result["status"] == "COMPLETED", result["summary"]
        assert result["summary"]["sample_only_gate"]["passed"]
        phases = [item["phase"] for item in result["runs"]]
        assert ("training" in phases) is (mode == "routing")
        assert ("generation" in phases) is (mode == "generation")
        assert phases.count("confirmation") == 1
        before = len(calls)
        response = control("POST", path + "/source-proposal", {"purpose": "owned-review"})
        assert response["version"] == "cutback.source-policy-proposal.v2"
        assert response["proposed"]["cache_execution"] == current.cache_execution.model_dump(
            mode="json"
        )
        assert (
            response["original_cache_requested"]
            is response["proposed_cache_requested"]
            is requested
        )
        assert response["full_task_cost_known"] is False
        if mode in {"static", "routing"}:
            destination = tmp_path / "private-cached-agent-handoff"
            handoff = finish(
                root,
                control,
                destination=destination,
                purpose="owned-cached-source-review",
                commands=[
                    {
                        "key": "native-contract",
                        "arguments": [
                            "-c",
                            "import sys,unittest; sys.path.insert(0, "
                            + repr(str(Path(cutback.__file__).resolve().parents[1]))
                            + "); unittest.main(module='test_app')",
                        ],
                        "timeout_seconds": 30,
                    }
                ],
                coverage={binding.workflow: ["native-contract"]},
                execute_local=True,
            )
            if mode == "static":
                # The independent application's contract rejects a cheap model
                # on long inputs, even when a synthetic evaluation said A. A
                # passed evaluation cannot override failed consumer checks.
                assert handoff["status"] == "CHECKS_FAILED", handoff
                checks = read_private(destination / "checks.json")
                assert checks["all_passed"] is False
                assert [item["status"] for item in checks["results"]] == ["passed", "failed"]
                assert not (destination / "review.json").exists()
            else:
                assert handoff["status"] == "REVIEWED", handoff
                review = read_private(destination / "review.json")
                assert review["status"] == (
                    "CACHE_RUNTIME_CONFIGURATION_REQUIRED"
                    if requested
                    else "READY_FOR_LOCAL_REVIEW"
                )
                assert review["cache_runtime_required"] is requested
                assert review["runtime_activation_change_authorized"] is False
        assert len(calls) == before
        assert bool(documents) is (mode == "generation")
    finally:
        executor.close()
