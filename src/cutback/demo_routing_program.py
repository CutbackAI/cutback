"""Independent source application through one approved budget and exact checked rollback."""

from uuid import uuid4

from cutback.core.workspace import (
    private_directory,
    read_private,
    write_private,
    write_private_text_output,
)


def run_sample(
    app,
    client,
    endpoint,
    admin,
    project,
    ingest_token,
    workspace,
    *,
    simple_controls_pass=False,
    unknown_usage=False,
):
    from cutback.agent import analyze, prepare
    from cutback.agent_control import Control
    from cutback.agent_routing_handoff import finish
    from cutback.agent_routing_program import advance, current, status
    from cutback.agent_routing_program import prepare as prepare_program
    from cutback.application_checks import git
    from cutback.demo_campaign_outcomes import frozen_tasks
    from cutback.demo_routing import synthetic_executor
    from cutback.routing_program_operator import RoutingProgramOperator
    from cutback.sample_app.routing_fixture import fixture

    channel = Control(endpoint, admin, client=client)
    writer = Control(endpoint, ingest_token, client=client)
    owned = private_directory(workspace, "routing-program-" + uuid4().hex)
    root = private_directory(owned, "application")
    for name, content in fixture().items():
        (root / name).write_text(content, encoding="utf-8", newline="")
    git(root, "init")
    git(root, "config", "user.name", "Synthetic Cutback Sample")
    git(root, "config", "user.email", "synthetic@example.invalid")
    git(root, "add", ".")
    git(root, "commit", "-m", "Independent routing program application")
    workflow = analyze(root)["routing_producers"][0]["workflow_id"]
    prepare(
        root,
        channel,
        project,
        files=["tickets.jsonl"],
        rights_basis="synthetic",
        rights_ref="owned-routing-program-sample",
        max_usd="100",
        max_requests=2000,
        workflow=workflow,
        minimum=1,
        independent_labels=True,
        representative=False,
        learned_routing=True,
        fixture_transport=True,
        min_test_samples=3,
    )
    baseline = frozen_tasks(
        channel, project, writer, workflow, "owned-program-baseline", ["success"], "1"
    )
    row = prepare_program(
        root,
        channel,
        baseline_snapshot_id=baseline["id"],
        max_usd="100",
        max_requests=2000,
        max_wall_seconds=600,
        savings_per_success="0.01",
        forecast_successful_tasks=100000,
    )
    advance(root, channel, program_hash=row["program_hash"], approval_ref="owned-synthetic-program")
    executor, calls = synthetic_executor(
        simple_controls_pass=simple_controls_pass, unknown_usage=unknown_usage
    )
    try:
        operator = RoutingProgramOperator(
            app.state.sessions,
            app.state.artifact_store,
            project_id=project,
            program_id=row["id"],
            program_hash=row["program_hash"],
            executor=executor,
            preflight=lambda: current(root, channel),
        )
        operator.run(max_operations=4, max_wait_seconds=600, poll_seconds=0.1)
        completed = status(root, channel)
        receipt = {
            "contract": "cutback.routing-program-sample.v1",
            "program_id": row["id"],
            "program_hash": row["program_hash"],
            "status": completed["status"],
            "phases": [run["phase"] for run in completed["runs"]],
            "native_sdk_calls": len(calls),
            "reserved_native_usd": completed["reserved_usd"],
            "known_native_spend_subtotal_usd": completed["known_spend_subtotal_usd"],
            "known_cost_requests": completed["known_cost_requests"],
            "independent_application": True,
            "synthetic_only": True,
            "real_model_quality_verified": False,
            "fit_compute_usd": None,
            "router_hosting_usd": None,
            "production_savings_usd": None,
            "deployment_authorized": False,
            "workspace": str(owned),
        }
        if unknown_usage:
            if completed["status"] != "FAILED" or not calls or completed["known_cost_requests"]:
                raise ValueError("ROUTING_PROGRAM_UNKNOWN_BILLING_DID_NOT_STOP")
            receipt["uncertain_billing_preserved_no_training_replay"] = True
            write_private(owned / "receipt.json", receipt)
            return receipt
        if (
            completed["status"] != "COMPLETED"
            or completed["summary"].get("code") != "confirmation_completed"
        ):
            raise ValueError("ROUTING_PROGRAM_SAMPLE_DID_NOT_CONFIRM")
        handoff = owned / "checked-handoff"
        preview = finish(
            root,
            channel,
            destination=handoff,
            purpose="owned-current-routing-program-review",
            branch="cutback/routing-program-review",
        )
        coverage = {"ticket-priority": preview["command_keys"]}
        reviewed = finish(
            root,
            channel,
            destination=handoff,
            purpose="owned-current-routing-program-review",
            coverage=coverage,
            execute_local=True,
            branch="cutback/routing-program-review",
        )
        if reviewed["status"] != "REVIEWED":
            raise ValueError("ROUTING_PROGRAM_SAMPLE_APPLICATION_CHECKS_DID_NOT_PASS")
        receipt.update(
            both_application_trees_checked=reviewed["both_application_trees_checked"],
            byte_exact_rollback_checked=reviewed["byte_exact_rollback_checked"],
            local_review_ref=reviewed["local_review_ref"],
            checked_handoff_workspace=str(handoff),
            base_application=reviewed["base_root"],
            proposed_application=reviewed["proposed_root"],
            source_change_applied=False,
        )
        for name in ("proposal.json", "checks.json", "contract.json", "review.json"):
            write_private(owned / name, read_private(handoff / name))
        for name in ("change.patch", "rollback.patch"):
            write_private_text_output(owned / name, (handoff / name).read_text(encoding="utf-8"))
        write_private(owned / "coverage.json", coverage)
        write_private(owned / "receipt.json", receipt)
        return receipt
    finally:
        executor.close()
