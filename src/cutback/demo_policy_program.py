"""Synthetic application through one whole approval, learned routing and checked rollback."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from cutback.core.workspace import private_directory, write_private


def run_sample(app, client, endpoint, admin, project, ingest_token, workspace):
    from cutback import __file__ as package_file
    from cutback.agent_control import Control
    from cutback.agent_policy_handoff import finish
    from cutback.agent_policy_program import advance, current, prepare, status
    from cutback.application_checks import git
    from cutback.demo_campaign_outcomes import frozen_tasks
    from cutback.demo_routing import sample_cases, synthetic_executor
    from cutback.policy_program_operator import SourcePolicyProgramOperator
    from cutback.sample_app.policy_fixture import fixture

    control, writer = (
        Control(endpoint, admin, client=client),
        Control(endpoint, ingest_token, client=client),
    )
    owned = private_directory(workspace, "source-policy-program-" + uuid4().hex)
    root = private_directory(owned, "application")
    for name, value in fixture().items():
        (root / name).write_text(value, encoding="utf-8", newline="")
    git(root, "init", "--template=")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Synthetic",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "Independent source-policy application",
    )
    workflow, base = "synthetic-source-policy", f"/v1/projects/{project}"
    policy = control(
        "POST",
        base + "/capture-policies",
        {
            "enabled": True,
            "approval_ref": "owned-source-policy-sample",
            "rights_basis": "synthetic",
            "environments": ["staging"],
            "workflows": [workflow],
            "allowed_providers": ["openai"],
            "sample_rate": 1,
            "max_samples": 4000,
            "max_payload_bytes": 512000,
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    dataset = control(
        "POST",
        base + "/datasets",
        {
            "workflow": workflow,
            "policy_id": policy["id"],
            "source_type": "synthetic_fixture",
            "provenance_ref": "independent-source-policy-example",
            "independent_labels": True,
            "split_policy": "task-aware-v2",
            "cases": sample_cases(),
        },
    )
    baseline = frozen_tasks(
        control, project, writer, workflow, "synthetic-policy-baseline", ["success"], "1"
    )
    row = prepare(
        root,
        control,
        project,
        producer_file="app.py",
        purpose="owned-source-policy-sample",
        max_usd="100",
        max_requests=2000,
        max_wall_seconds=600,
        savings_per_success="0.01",
        forecast_successful_tasks=100000,
        minimum=1,
        min_test_samples=3,
        min_test_groups=3,
        generate=False,
        fixture_transport=True,
    )
    discovery = current(root, control)["preparation_data_selection"]
    if (
        discovery["selected_dataset_id"] != dataset["id"]
        or discovery["selected_baseline_snapshot_id"] != baseline["id"]
    ):
        raise ValueError("SOURCE_POLICY_SAMPLE_DATA_DISCOVERY_CHANGED")
    advance(
        root,
        control,
        program_hash=row["program_hash"],
        approval_ref="owned-synthetic-policy-budget",
    )
    executor, calls = synthetic_executor()
    try:
        SourcePolicyProgramOperator(
            app.state.sessions,
            app.state.artifact_store,
            project_id=project,
            program_id=row["id"],
            program_hash=row["program_hash"],
            executor=executor,
            preflight=lambda: current(root, control),
        ).run(max_operations=10, max_wait_seconds=600, poll_seconds=0.1)
        completed = status(root, control)
        if (
            completed["status"] != "COMPLETED"
            or completed["summary"].get("sample_only_gate", {}).get("passed") is not True
        ):
            raise ValueError("SOURCE_POLICY_SAMPLE_DID_NOT_CONFIRM")
        commands = [
            {
                "key": "native-policy-application",
                "arguments": [
                    "-c",
                    "import sys, runpy; sys.path.insert(0, "
                    + repr(str(Path(package_file).resolve().parents[1]))
                    + "); runpy.run_path('check_application.py', run_name='__main__')",
                ],
                "timeout_seconds": 60,
            }
        ]
        handoff = finish(
            root,
            control,
            destination=owned / "checked-handoff",
            purpose="owned-source-policy-review",
            commands=commands,
            coverage={workflow: ["native-policy-application"]},
            execute_local=True,
            branch="cutback/source-policy-sample",
        )
        if handoff["status"] != "REVIEWED":
            raise ValueError("SOURCE_POLICY_SAMPLE_APPLICATION_CHECKS_FAILED")
        result = {
            "contract": "cutback.source-policy-sample.v1",
            "program_id": row["id"],
            "program_hash": row["program_hash"],
            "status": completed["status"],
            "phases": [value["phase"] for value in completed["runs"]],
            "native_sdk_calls": len(calls),
            "known_cost_requests": completed["known_cost_requests"],
            "compatible_data_selected_automatically": True,
            "known_spend_subtotal_usd": completed["known_spend_subtotal_usd"],
            "paid_provider_calls": 0,
            "synthetic_only": True,
            "real_model_quality_verified": False,
            "production_savings_usd": None,
            "fit_compute_usd": None,
            "routing_hosting_usd": None,
            "source_directory": str(root),
            "workspace": str(owned),
            "checked_handoff": handoff,
            "original_application_changed": False,
            "deployment_authorized": False,
        }
        write_private(owned / "receipt.json", result)
        write_private(
            root / ".cutback/agent/connection.json", {"endpoint": endpoint, "project": project}
        )
        # The connection intentionally omits every collector/provider credential.
        return json.loads(json.dumps(result))
    finally:
        executor.close()
