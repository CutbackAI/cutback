"""Synthetic application-agent routing with actual native SDK, PG and both-tree checks."""

import hashlib
import shutil
from uuid import UUID


def seed_source_routing(app, control, project_id, workspace, *, execute=True, demo=False):
    from cutback.agent import advance, analyze, prepare, proposal
    from cutback.application_checks import (
        ApplicationCommand,
        git,
        run_checks,
    )
    from cutback.application_checks import (
        prepare as check_contract,
    )
    from cutback.core.workspace import write_private
    from cutback.demo import require_demo_git
    from cutback.demo_routing import synthetic_executor
    from cutback.routing_changes import inspect_configuration, review_change
    from cutback.sample_app.routing_fixture import fixture
    from cutback.server.worker import run_one

    require_demo_git()
    root = workspace / "source-routing"
    root.mkdir(mode=0o700)
    for name, text in fixture().items():
        (root / name).write_text(text, encoding="utf-8", newline="")
    git(root, "init")
    git(root, "config", "user.name", "Synthetic Cutback Sample")
    git(root, "config", "user.email", "synthetic@example.invalid")
    git(root, "add", ".")
    git(root, "commit", "-m", "Independent source routing baseline")
    baseline, binding = inspect_configuration(root, "app.py")
    write_private(
        workspace / "routing-inspection.json",
        {
            "baseline": baseline.model_dump(mode="json"),
            "source": binding.model_dump(mode="json"),
        },
    )

    class OwnedControl:
        endpoint = str(control.base_url).rstrip("/")

        def __call__(self, method, path, body=None):
            response = control.request(method, path, **({"json": body} if body is not None else {}))
            if response.is_error:
                raise ValueError(
                    "SYNTHETIC_SOURCE_ROUTING_CONTROL_FAILED_" + str(response.status_code)
                )
            return response.json()

    channel = OwnedControl()
    analyzed = analyze(root)
    workflow = analyzed["routing_producers"][0]["workflow_id"]
    state = prepare(
        root,
        channel,
        project_id,
        files=["tickets.jsonl"],
        rights_basis="synthetic",
        rights_ref="owned-source-routing-demo",
        max_usd="4",
        max_requests=500,
        workflow=workflow,
        minimum=1,
        independent_labels=True,
        representative=False,
        learned_routing=True,
        fixture_transport=True,
        min_test_samples=3,
    )
    if demo:
        app.state.demo_routing_project = str(project_id)
    receipt = {
        "workflow": workflow,
        "dataset_id": state["dataset_id"],
        "policy_id": state["policy_id"],
        "training_plan_id": state["plan"]["id"],
        "training_plan_hash": state["plan"]["plan_hash"],
        "source_bound": True,
        "paid_provider_calls": 0,
        "production_changed": False,
        "full_task_cost_known": False,
        "source_review_ready": False,
        "disclosure": (
            "All native replies and usage are synthetic; no customer savings or model benchmark."
        ),
    }
    if not execute:
        return receipt
    executor, calls = synthetic_executor()
    try:
        for stage in ("training", "validation", "confirmation"):
            if state["phase"] != "PLAN_READY" or state["routing"]["stage"] != stage:
                raise ValueError("SYNTHETIC_SOURCE_ROUTING_STAGE_CHANGED")
            receipt[stage + "_plan_id"] = state["plan"]["id"]
            queued = advance(
                root,
                channel,
                plan_hash=state["plan"]["plan_hash"],
                approval_ref="owned-synthetic-" + stage,
            )
            result = run_one(
                app.state.sessions,
                app.state.artifact_store,
                executor=executor,
                execution_enabled=True,
                only_run_id=UUID(queued["run_id"]),
            )
            if result["status"] != "COMPLETED":
                raise ValueError("SYNTHETIC_SOURCE_ROUTING_STAGE_FAILED")
            receipt[stage + "_run_id"] = queued["run_id"]
            state = advance(root, channel)
        artifact = proposal(root, channel, purpose="owned-synthetic-source-review")
        write_private(workspace / "routing-source-change.json", artifact)
        proposed = workspace / "source-routing-proposed"
        shutil.copytree(root, proposed, ignore=shutil.ignore_patterns(".cutback", "__pycache__"))
        patch = workspace / "routing-source.patch"
        patch.write_text(artifact["diff"], encoding="utf-8", newline="")
        git(proposed, "apply", "--check", str(patch))
        git(proposed, "apply", str(patch))
        git(proposed, "add", "routing.json")
        git(proposed, "commit", "-m", "Review synthetic learned routing policy")
        contract = check_contract(
            root,
            proposed,
            [
                ApplicationCommand(
                    key="native-application",
                    arguments=["check_application.py"],
                )
            ],
        )
        checked = run_checks(contract, root, proposed, execute_local=True)
        if proposal(root, channel, purpose="owned-synthetic-current-review") != artifact:
            raise ValueError("SYNTHETIC_SOURCE_ROUTING_PROPOSAL_CHANGED")
        review = review_change(
            artifact,
            contract,
            checked,
            root,
            proposed,
            {"ticket-priority": ["native-application"]},
            branch="cutback/source-routing-review",
        )
        rollback = workspace / "routing-source.rollback.patch"
        rollback.write_text(artifact["rollback_diff"], encoding="utf-8", newline="")
        git(proposed, "apply", "--check", str(rollback))
        git(proposed, "apply", str(rollback))
        if (proposed / "routing.json").read_bytes() != (root / "routing.json").read_bytes():
            raise ValueError("SYNTHETIC_SOURCE_ROUTING_ROLLBACK_CHANGED")
        git(proposed, "apply", str(patch))
        if (
            hashlib.sha256((proposed / "routing.json").read_bytes()).hexdigest()
            != artifact["proposed_sha256"]
        ):
            raise ValueError("SYNTHETIC_SOURCE_ROUTING_RESTORED_POLICY_CHANGED")
        write_private(workspace / "routing-source-checks.json", checked)
        write_private(workspace / "routing-source-review.json", review)
        receipt.update(
            fit_id=state["routing"]["fit_id"],
            native_sdk_calls=len(calls),
            actual_declared_checks_passed=True,
            source_review_ready=True,
            both_application_trees_checked=True,
            byte_exact_rollback_checked=True,
            local_review_ref=review["local_review_ref"],
        )
        return receipt
    finally:
        executor.close()
