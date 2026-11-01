"""Fresh core-plus-HTTPX wheel prepares a source program over actual scoped HTTP."""

import json
import os
import subprocess
from uuid import UUID

import pytest
from integration.test_source_policy_program_api import prepared_program
from support_http import owned_collector
from support_policy_application import application
from support_remote_agent import remote_environment, remote_interpreter, remote_json

from cutback.policy_source import inspect_configuration

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize(
    "current_routed,async_consumer,with_cache",
    [(False, False, False), (False, False, True), (True, False, False), (False, True, False)],
)
def test_minimal_remote_program_prepare_reconcile_status_and_cancel_without_sdk_or_content_logs(
    api, tenant, tmp_path, monkeypatch, current_routed, async_consumer, with_cache
):
    configured = os.getenv("CUTBACK_TEST_REMOTE_PYTHON")
    if not configured:
        pytest.skip("set CUTBACK_TEST_REMOTE_PYTHON to a fresh matching core-plus-HTTPX wheel")
    executable, env = remote_interpreter(configured), remote_environment()
    probe = subprocess.run(
        [
            str(executable),
            "-I",
            "-c",
            "import importlib.util,sys; from pathlib import Path; import cutback; "
            "import cutback.agent_policy_program; "
            "import cutback.agent_policy_data; "
            "assert Path(cutback.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()); "
            "assert all(importlib.util.find_spec(name) is None for name in "
            "('openai','anthropic','fastapi','sqlalchemy','alembic','psycopg','cryptography',"
            "'jsonschema','sklearn','pytest'))",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert probe.returncode == 0, "POLICY_PROGRAM_REQUIRES_MATCHING_MINIMAL_WHEEL"
    root = application(tmp_path, current_routed=current_routed, async_consumer=async_consumer)
    _, binding = inspect_configuration(root, "app.py")
    base, _, dataset, _, executor, calls, _, _, request = prepared_program(
        api, tenant, tmp_path, monkeypatch, source_binding=binding, current_routed=current_routed
    )
    admin = api[1]["Authorization"][7:]
    env["CUTBACK_CONTROL_TOKEN"] = admin
    prefix = [str(executable), "-I", "-m", "cutback.cli", "source-policy"]

    def invoke(arguments):
        result = subprocess.run(
            prefix + arguments,
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert admin not in result.stdout + result.stderr
        assert "OWNED_PRIVATE_POLICY_INSTRUCTION" not in result.stdout + result.stderr
        assert result.returncode == 0, "MINIMAL_SOURCE_POLICY_ACTION_FAILED_CONTENT_OMITTED"
        return remote_json(result)

    try:
        with owned_collector(api[2]) as endpoint:
            arguments = [
                "prepare",
                "--target",
                str(root),
                "--endpoint",
                endpoint,
                "--project",
                tenant[0],
                "--producer",
                "app.py",
                "--purpose-reference",
                "owned-minimal-policy",
                "--max-usd",
                "100",
                "--max-requests",
                "1000",
                "--savings-per-success",
                "0.01",
                "--forecast-successful-tasks",
                "100000",
                "--no-generate",
                "--fixture-transport",
                "--minimum",
                "1",
                "--min-test-samples",
                "3",
                "--min-test-groups",
                "3",
            ]
            if with_cache:
                from unit.test_policy_workload_guards import contract

                cache_contract = tmp_path / "owned-cache-contract.json"
                cache_contract.write_text(json.dumps(contract()), encoding="utf-8")
                arguments += ["--exact-cache-contract", str(cache_contract)]
            row = invoke(arguments)
            assert row["status"] == "READY_FOR_APPROVAL"
            selection = row["local_preparation_data_selection"]
            assert selection["selected_dataset_id"] == dataset["id"]
            assert selection["selected_baseline_snapshot_id"] == request["baseline_snapshot_id"]
            assert selection["encrypted_storage_reads"] == selection["provider_calls"] == 0
            if with_cache:
                assert row["local_preparation_workload_assessment"]["decision"] == "abstain"
                assert row["local_cache_control_decision"]["included_in_preview"] is True
                assert row["local_cache_control_decision"]["basis"] == (
                    "frozen_synthetic_validation_inputs_only"
                )
            assert row["economic_envelope"]["reference_kind"] == (
                "current_source_router" if current_routed else "current_source_native"
            )
            assert invoke(arguments)["id"] == row["id"]
            assert invoke(["status", "--target", str(root)])["program_hash"] == row["program_hash"]
            assert invoke(["cancel", "--target", str(root)])["status"] == "CANCELLED"
            with api[2].state.sessions() as session:
                from sqlalchemy import select

                from cutback.server.policy_program_db import SourcePolicyProgramRecord

                assert (
                    len(
                        session.scalars(
                            select(SourcePolicyProgramRecord).where(
                                SourcePolicyProgramRecord.project_id == UUID(tenant[0])
                            )
                        ).all()
                    )
                    == 2
                )
            assert (
                api[0]
                .get(base + "/source-policy-programs/" + row["id"], headers=api[1])
                .json()["requests"]
                == 0
            )
            for path in (root / ".cutback/agent").glob("*.json"):
                assert admin not in path.read_text("utf-8")
                assert "OWNED_PRIVATE_POLICY_INSTRUCTION" not in path.read_text("utf-8")
            assert calls == []
    finally:
        executor.close()
