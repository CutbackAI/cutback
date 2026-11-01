"""HTTP-only agent continuation for one source-bound routing envelope."""

import math
import time
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import TypeAdapter

from cutback.core.models import Identifier
from cutback.core.workspace import read_private, workspace_lock, write_private

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED", "ABSTAINED"}


def check_receipt_scope(saved, row):
    template = row.get("specification", {}).get("template", {})
    execution = template.get("routing_execution") or {}
    if (
        row.get("template_plan_id") != saved["request"]["template_plan_id"]
        or template.get("workflow") != saved["workflow"]
        or execution.get("source") != saved["source"]
    ):
        raise ValueError("ROUTING_PROGRAM_COLLECTOR_TEMPLATE_OR_SOURCE_CHANGED")


def current(root, control):
    from cutback.agent import load_session, workspace
    from cutback.agent_routing import selected_source

    root, directory = workspace(root)
    state = load_session(root)
    saved = read_private(directory / "routing-program.json")
    if (
        saved["endpoint"] != control.endpoint
        or state["endpoint"] != control.endpoint
        or saved["project_id"] != state["project_id"]
        or saved["workflow"] != state["selected_workflow"]
        or saved["analysis_id"] != state["id"]
    ):
        raise ValueError("ROUTING_PROGRAM_SAVED_SCOPE_OR_DESTINATION_CHANGED")
    _, binding, _ = selected_source(root, state, saved["workflow"])
    if binding.model_dump(mode="json") != saved["source"]:
        raise ValueError("ROUTING_PROGRAM_SAVED_SOURCE_CHANGED")
    return saved


def prepare(
    root,
    control,
    *,
    baseline_snapshot_id,
    max_usd,
    max_requests=1000,
    max_wall_seconds=600,
    savings_per_success=None,
    forecast_successful_tasks=None,
):
    from cutback.agent import load_session, save_session, workspace
    from cutback.agent_routing import selected_source

    root, directory = workspace(root)
    with workspace_lock(directory):
        state = load_session(root)
        if (
            state.get("search_mode") != "source_routing"
            or state.get("phase") != "PLAN_READY"
            or state.get("routing", {}).get("stage") != "training"
            or state["endpoint"] != control.endpoint
        ):
            raise ValueError("ROUTING_PROGRAM_REQUIRES_PREPARED_SOURCE_TRAINING_TEMPLATE")
        _, binding, _ = selected_source(root, state, state["selected_workflow"])
        if control("GET", "/v1/health/ready").get("routing_program_contract") != (
            "cutback.routing-improvement-program.v1"
        ):
            raise ValueError("ROUTING_PROGRAM_COLLECTOR_UPGRADE_REQUIRED")
        body = {
            "template_plan_id": state["plan"]["id"],
            "baseline_snapshot_id": str(UUID(str(baseline_snapshot_id))),
            "budget": {
                "max_usd": str(Decimal(max_usd)),
                "max_requests": max_requests,
                "max_wall_seconds": max_wall_seconds,
            },
            "conservative_savings_per_success_usd": savings_per_success,
            "forecast_successful_tasks": forecast_successful_tasks,
        }
        path = directory / "routing-program.json"
        if path.exists():
            saved = current(root, control)
            if saved["request"] != body:
                raise ValueError("ROUTING_PROGRAM_FROZEN_INTENT_CHANGED")
        else:
            execution = control(
                "GET",
                f"/v1/projects/{state['project_id']}/experiments/plans/{state['plan']['id']}/execution",
            )
            if execution.get("run_id") or execution.get("approval_status") != "pending":
                raise ValueError("ROUTING_PROGRAM_TEMPLATE_EXECUTION_ALREADY_STARTED")
            saved = {
                "request": body,
                "request_id": str(uuid4()),
                "analysis_id": state["id"],
                "project_id": state["project_id"],
                "endpoint": control.endpoint,
                "workflow": state["selected_workflow"],
                "source": binding.model_dump(mode="json"),
            }
            write_private(path, saved)
        state["routing_program"] = {
            "request_id": saved["request_id"],
            "program_id": saved.get("program_id"),
            "program_hash": saved.get("program_hash"),
        }
        save_session(directory, state)
        receipt = control(
            "POST",
            f"/v1/projects/{saved['project_id']}/routing/programs",
            {**body, "request_id": saved["request_id"]},
        )
        check_receipt_scope(saved, receipt)
        saved.update(program_id=receipt["id"], program_hash=receipt["program_hash"])
        write_private(path, saved)
        state["routing_program"].update(
            program_id=receipt["id"], program_hash=receipt["program_hash"]
        )
        save_session(directory, state)
        return receipt


def status(root, control):
    saved = current(root, control)
    if not saved.get("program_id"):
        raise ValueError("ROUTING_PROGRAM_PREPARATION_REPLY_UNCERTAIN_RESUME_SAME_INTENT")
    row = control(
        "GET", f"/v1/projects/{saved['project_id']}/routing/programs/{saved['program_id']}"
    )
    check_receipt_scope(saved, row)
    if row.get("id") != saved["program_id"] or row.get("program_hash") != saved["program_hash"]:
        raise ValueError("ROUTING_PROGRAM_COLLECTOR_IDENTITY_CHANGED")
    return row


def advance(root, control, *, program_hash=None, approval_ref=None):
    from cutback.agent import workspace

    _, directory = workspace(root)
    with workspace_lock(directory):
        saved = current(root, control)
        row = status(root, control)
        if row["status"] != "READY_FOR_APPROVAL":
            return row
        if saved.get("approval_dispatched"):
            return {**row, "operator_code": "APPROVAL_REPLY_UNCERTAIN_RECONCILE_SAME_PROGRAM"}
        if program_hash != saved["program_hash"] or not approval_ref:
            raise ValueError("EXACT_ROUTING_PROGRAM_HASH_AND_APPROVAL_REFERENCE_REQUIRED")
        TypeAdapter(Identifier).validate_python(approval_ref)
        saved["approval_dispatched"] = True
        write_private(directory / "routing-program.json", saved)
        return control(
            "POST",
            f"/v1/projects/{saved['project_id']}/routing/programs/{saved['program_id']}/approve",
            {"program_hash": program_hash, "approval_ref": approval_ref},
        )


def run(root, control, *, max_wait_seconds=300, poll_seconds=1, max_polls=300, emit=None):
    """Observe a remote worker; never approve, enqueue, reset or replay anything."""
    if (
        type(max_wait_seconds) is not int
        or not 1 <= max_wait_seconds <= 3600
        or type(max_polls) is not int
        or not 1 <= max_polls <= 3600
        or type(poll_seconds) not in {float, int}
        or not math.isfinite(poll_seconds)
        or not 0.1 <= poll_seconds <= 10
    ):
        raise ValueError("ROUTING_PROGRAM_POLL_AND_WAIT_BOUNDS_REQUIRED")
    stop, previous = time.monotonic() + max_wait_seconds, None
    for _ in range(max_polls):
        row = status(root, control)
        identity = (row["status"], row["requests"], row["known_cost_requests"])
        if emit and identity != previous:
            emit(
                {
                    key: row[key]
                    for key in (
                        "id",
                        "program_hash",
                        "status",
                        "requests",
                        "known_cost_requests",
                        "reserved_usd",
                        "known_spend_subtotal_usd",
                    )
                }
            )
        previous = identity
        if row["status"] in TERMINAL or row["approval_status"] != "consumed":
            return row
        remaining = stop - time.monotonic()
        if remaining <= 0:
            return {**row, "operator_code": "WAIT_BOUND_REACHED_RESUME_SAME_PROGRAM"}
        time.sleep(min(poll_seconds, remaining))
    return {**row, "operator_code": "POLL_BOUND_REACHED_RESUME_SAME_PROGRAM"}


def proposal(root, control, *, purpose):
    from cutback.agent import workspace
    from cutback.routing_changes import RoutingSourceProposal, prepare_patch

    root, directory = workspace(root)
    with workspace_lock(directory):
        saved = current(root, control)
        TypeAdapter(Identifier).validate_python(purpose)
        source = RoutingSourceProposal.model_validate(
            control(
                "POST",
                f"/v1/projects/{saved['project_id']}/routing/programs/{saved['program_id']}/source-proposal",
                {"purpose": purpose},
            )
        )
        if source.binding.model_dump(mode="json") != saved["source"]:
            raise ValueError("ROUTING_PROGRAM_CONFIRMED_SOURCE_CHANGED")
        artifact = prepare_patch(root, source)
        write_private(directory / "routing-program-proposal.json", artifact)
        return artifact


def review(root, control, *, purpose, contract, receipt, proposed_root, coverage, branch=None):
    from cutback.agent import workspace
    from cutback.routing_changes import review_change

    # Refresh canonical authority before trusting any saved local proposal.
    artifact = proposal(root, control, purpose=purpose)
    root, directory = workspace(root)
    with workspace_lock(directory):
        current(root, control)
        result = review_change(
            artifact, contract, receipt, root, proposed_root, coverage, branch=branch
        )
        write_private(directory / "routing-program-review.json", result)
        return result
