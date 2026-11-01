"""Actual private source/check handoff after an HTTP-prepared common policy program."""

from pathlib import Path
from uuid import UUID

import pytest
from integration.test_source_policy_program_api import prepared_program
from integration.test_source_policy_program_workflow import drive
from sqlalchemy import select
from support_policy_application import application

import cutback
from cutback.agent_control import Control
from cutback.agent_policy_handoff import finish
from cutback.agent_policy_program import advance, prepare
from cutback.agent_policy_publication import publish_review
from cutback.application_checks import snapshot
from cutback.core.workspace import read_private
from cutback.policy_source import inspect_configuration
from cutback.server.db import CapturePolicyRecord, PayloadSample

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize(
    "current_routed,revoke_after_checks,async_consumer",
    [
        (False, False, False),
        (False, True, False),
        (True, False, False),
        (True, True, False),
        (False, False, True),
    ],
)
def test_http_agent_prepares_one_budget_and_checks_exact_policy_and_rollback_without_source_changes(
    api, tenant, tmp_path, monkeypatch, revoke_after_checks, current_routed, async_consumer
):
    root = application(tmp_path, current_routed=current_routed, async_consumer=async_consumer)
    _, binding = inspect_configuration(root, "app.py")
    assert binding.async_consumer is async_consumer
    base, store, dataset, policy_id, executor, calls, _, unused, request = prepared_program(
        api, tenant, tmp_path, monkeypatch, source_binding=binding, current_routed=current_routed
    )
    control = Control("http://127.0.0.1", api[1]["Authorization"][7:], client=api[0])
    options = {
        "dataset_id": dataset["id"],
        "baseline_snapshot_id": request["baseline_snapshot_id"],
        "producer_file": "app.py",
        "purpose": "owned-synthetic-policy-search",
        "max_usd": "100",
        "max_requests": 1000,
        "max_wall_seconds": 600,
        "savings_per_success": "0.01",
        "forecast_successful_tasks": 100000,
        "minimum": 1,
        "min_test_samples": 3,
        "min_test_groups": 3,
        "generate": False,
        "fixture_transport": True,
    }
    try:
        before = snapshot(root)
        row = prepare(root, control, tenant[0], **options)
        assert row["status"] == "READY_FOR_APPROVAL"
        journal = read_private(root / ".cutback/agent/policy-program.json")
        assert "OWNED_PRIVATE" not in str(journal)
        with api[2].state.sessions() as session:
            count = len(
                session.scalars(
                    select(PayloadSample).where(PayloadSample.project_id == UUID(tenant[0]))
                ).all()
            )
        assert prepare(root, control, tenant[0], **options)["id"] == row["id"]
        with api[2].state.sessions() as session:
            assert (
                len(
                    session.scalars(
                        select(PayloadSample).where(PayloadSample.project_id == UUID(tenant[0]))
                    ).all()
                )
                == count
            )
        advance(
            root, control, program_hash=row["program_hash"], approval_ref="owned-program-fixture"
        )
        path = base + "/source-policy-programs/" + row["id"]
        completed = drive(api, store, executor, row, path)
        assert completed["summary"].get("sample_only_gate", {}).get("passed") is True, completed[
            "summary"
        ]
        native_count = len(calls)
        destination = tmp_path / "private-checked-policy"
        commands = [
            {
                "key": "native-application-contract",
                "arguments": [
                    "-c",
                    "import sys, unittest; sys.path.insert(0, "
                    + repr(str(Path(cutback.__file__).resolve().parents[1]))
                    + "); unittest.main(module='test_app')",
                ],
                "timeout_seconds": 30,
            }
        ]
        coverage = {binding.workflow: ["native-application-contract"]}
        preview = finish(
            root,
            control,
            destination=destination,
            purpose="owned-source-review",
            commands=commands,
            branch="cutback/source-policy-review",
        )
        assert preview["status"] == "CHECKS_READY", preview
        if revoke_after_checks:
            import cutback.agent_policy_handoff as handoff

            original = handoff.run_checks

            def checked_then_revoke(*args, **kwargs):
                result = original(*args, **kwargs)
                with api[2].state.sessions.begin() as session:
                    session.get(CapturePolicyRecord, UUID(policy_id)).enabled = False
                return result

            monkeypatch.setattr(handoff, "run_checks", checked_then_revoke)
            with pytest.raises(ValueError, match="CURRENT_CONFIRMED_SOURCE"):
                finish(
                    root,
                    control,
                    destination=destination,
                    purpose="owned-source-review",
                    commands=commands,
                    coverage=coverage,
                    execute_local=True,
                    branch="cutback/source-policy-review",
                )
            assert not (destination / "review.json").exists()
        else:
            result = finish(
                root,
                control,
                destination=destination,
                purpose="owned-source-review",
                commands=commands,
                coverage=coverage,
                execute_local=True,
                branch="cutback/source-policy-review",
            )
            assert result["status"] == "REVIEWED", result
            assert read_private(destination / "checks.json")["all_passed"] is True
            assert result["byte_exact_rollback_checked"] is True
            assert read_private(destination / "review.json")["status"] == "READY_FOR_LOCAL_REVIEW"
            assert (
                finish(
                    root,
                    control,
                    destination=destination,
                    purpose="owned-source-review",
                    commands=commands,
                    coverage=coverage,
                    execute_local=True,
                    branch="cutback/source-policy-review",
                )["status"]
                == "REVIEWED"
            )
            with pytest.raises(ValueError, match="POLICY_HELDOUT_REVIEW_REQUIRED"):
                publish_review(
                    root,
                    control,
                    purpose="owned-synthetic-publication-refusal",
                    repository="example-owner/policy-app",
                    base_branch="main",
                    head_branch="cutback/checked-policy",
                    publish=True,
                    api=lambda *args: pytest.fail("SYNTHETIC_GATE_MUST_NOT_CONTACT_GITHUB"),
                )
            if not current_routed and not async_consumer:
                with api[2].state.sessions.begin() as session:
                    session.get(CapturePolicyRecord, UUID(policy_id)).enabled = False
                with pytest.raises(ValueError, match="CURRENT_CONFIRMED_SOURCE"):
                    publish_review(
                        root,
                        control,
                        purpose="owned-revoked-publication-refusal",
                        repository="example-owner/policy-app",
                        base_branch="main",
                        head_branch="cutback/checked-policy",
                        publish=True,
                        api=lambda *args: pytest.fail("REVOKED_REVIEW_MUST_NOT_CONTACT_GITHUB"),
                    )
        assert snapshot(root) == before and len(calls) == native_count
    finally:
        executor.close()
