"""Independent source application through one approved budget and exact checked rollback."""

import shutil
from uuid import uuid4

from cutback.core.workspace import private_directory, write_private


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
    from cutback.agent_routing_program import advance, current, proposal, review, status
    from cutback.agent_routing_program import prepare as prepare_program
    from cutback.application_checks import ApplicationCommand, git, run_checks
    from cutback.application_checks import prepare as check_contract
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
        artifact = proposal(root, channel, purpose="owned-routing-program-proposal")
        proposed = owned / "proposed-application"
        shutil.copytree(root, proposed, ignore=shutil.ignore_patterns(".cutback", "__pycache__"))
        patch, rollback = owned / "change.patch", owned / "rollback.patch"
        patch.write_text(artifact["diff"], encoding="utf-8", newline="")
        rollback.write_text(artifact["rollback_diff"], encoding="utf-8", newline="")
        git(proposed, "apply", "--check", str(patch))
        git(proposed, "apply", str(patch))
        git(proposed, "add", "routing.json")
        git(proposed, "commit", "-m", "Review independent confirmed routing program")
        contract = check_contract(
            root,
            proposed,
            [ApplicationCommand(key="native-application", arguments=["check_application.py"])],
        )
        checked = run_checks(contract, root, proposed, execute_local=True)
        reviewed = review(
            root,
            channel,
            purpose="owned-current-routing-program-review",
            contract=contract,
            receipt=checked,
            proposed_root=proposed,
            coverage={"ticket-priority": ["native-application"]},
            branch="cutback/routing-program-review",
        )
        git(proposed, "apply", "--check", str(rollback))
        git(proposed, "apply", str(rollback))
        if (proposed / "routing.json").read_bytes() != (root / "routing.json").read_bytes():
            raise ValueError("ROUTING_PROGRAM_SAMPLE_ROLLBACK_BYTES_CHANGED")
        git(proposed, "apply", str(patch))
        receipt.update(
            both_application_trees_checked=True,
            byte_exact_rollback_checked=True,
            local_review_ref=reviewed["local_review_ref"],
            source_change_applied=False,
        )
        write_private(owned / "proposal.json", artifact)
        write_private(owned / "checks.json", checked)
        write_private(owned / "contract.json", contract.model_dump(mode="json"))
        write_private(owned / "coverage.json", {"ticket-priority": ["native-application"]})
        write_private(owned / "review.json", reviewed)
        write_private(owned / "receipt.json", receipt)
        return receipt
    finally:
        executor.close()
