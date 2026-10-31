"""Finish one approved workflow stage without granting another stage authority.

Remote mode polls the existing worker. Local mode uses the same scoped worker as
``workflow work``. Neither mode approves, queues or replays an uncertain charge.
Source preparation, independent confirmation and application edits stay explicit.
"""

import math
import time
from uuid import UUID

OPERATOR_CONTRACT = "cutback.workflow-operator.v1"
ACTIVE = {"APPROVING", "QUEUEING", "QUEUED", "RUNNING"}
ROUTING_SETUP = {"PREPARING_ROUTING_FIT", "PREPARING_ROUTING_STAGE"}
MODES = {
    None,
    "qualified_model_swap",
    "model_and_output_cap",
    "exact_cache",
    "joint_model_context",
    "source_routing",
    "retrieval_search",
    "joint_model_retrieval",
    "reviewed_criteria",
}


def identity(state):
    return tuple(
        state.get(key)
        for key in (
            "id",
            "analysis_sha256",
            "endpoint",
            "project_id",
            "selected_workflow",
            "search_mode",
        )
    )


def preflight(root, state):
    if state.get("search_mode") == "source_routing":
        from cutback.agent_routing import selected_source
    else:
        from cutback.agent import selected_profile as selected_source

    selected_source(root, state, state["selected_workflow"])


def run_workflow(
    root,
    control,
    *,
    max_wait_seconds=300,
    poll_seconds=1,
    max_polls=300,
    local_worker=False,
    emit=None,
):
    """Wait for this already queued stage; never reuse approval for a new plan.

    The local wait limit bounds polling, not a running provider call. An explicit
    local worker keeps its frozen native wall bound and canonical charge ledger.
    Network failures propagate immediately. Rerunning reconciles saved identity.
    """
    from cutback.agent import advance, load_session, public_session

    if type(max_wait_seconds) is not int or not 1 <= max_wait_seconds <= 3600:
        raise ValueError("WORKFLOW_OPERATOR_WAIT_ONE_TO_3600_SECONDS_REQUIRED")
    if (
        type(poll_seconds) not in {int, float}
        or not math.isfinite(poll_seconds)
        or not 0.1 <= poll_seconds <= 30
    ):
        raise ValueError("WORKFLOW_OPERATOR_POLL_BOUND_REQUIRED")
    if type(max_polls) is not int or not 1 <= max_polls <= 1000:
        raise ValueError("WORKFLOW_OPERATOR_ONE_TO_1000_POLLS_REQUIRED")
    if type(local_worker) is not bool:
        raise ValueError("WORKFLOW_OPERATOR_LOCAL_WORKER_FLAG_REQUIRED")
    initial = load_session(root)
    expected = identity(initial)
    if initial.get("endpoint") != control.endpoint:
        raise ValueError("COLLECTOR_DESTINATION_MISMATCH")
    if initial.get("search_mode") not in MODES:
        raise ValueError("WORKFLOW_OPERATOR_REQUIRES_QUALIFIED_STAGE")
    original_plan = initial.get("plan", {}).get("id")
    original_hash = initial.get("plan", {}).get("plan_hash")
    original_run = initial.get("run_id")
    stop = time.monotonic() + max_wait_seconds
    polls, attempts, attempted = 0, 0, False

    class WaitBoundReached(Exception):
        pass

    def current():
        state = load_session(root)
        if identity(state) != expected:
            raise ValueError("WORKFLOW_OPERATOR_SAVED_SCOPE_CHANGED")
        return state

    def result(state, code):
        return {
            "workflow": public_session(state),
            "operator": {
                "version": OPERATOR_CONTRACT,
                "code": code,
                "polls": polls,
                "local_worker_attempts": attempts,
                "approval_performed": False,
                "queue_performed": False,
                "automatic_billable_retry": False,
                "production_changed": False,
                "provider_calls_made_by_polling": 0,
            },
        }

    class ScopedControl:
        endpoint = control.endpoint

        def __call__(self, method, path, body=None):
            # advance may perform nonbillable routing-fit preparation. It must
            # never approve or queue, even if another process changes the journal.
            state = current()
            if time.monotonic() >= stop:
                raise WaitBoundReached
            if method == "POST" and (path.endswith("/approve") or path.endswith("/experiments")):
                raise ValueError("WORKFLOW_OPERATOR_CANNOT_APPROVE_OR_QUEUE")
            if not path.startswith(f"/v1/projects/{initial['project_id']}/"):
                raise ValueError("WORKFLOW_OPERATOR_COLLECTOR_SCOPE_CHANGED")
            if (
                state.get("plan", {}).get("id") != original_plan
                or state.get("plan", {}).get("plan_hash") != original_hash
                or original_run is not None
                and state.get("run_id") != original_run
            ):
                raise ValueError("WORKFLOW_OPERATOR_PLAN_OR_RUN_CHANGED")
            return control(method, path, body)

    while True:
        state = current()
        if state["phase"] == "PLAN_READY":
            return result(state, "APPROVAL_REQUIRED")
        if (
            state.get("plan", {}).get("id") != original_plan
            or state.get("plan", {}).get("plan_hash") != original_hash
            or original_run is not None
            and state.get("run_id") != original_run
        ):
            raise ValueError("WORKFLOW_OPERATOR_PLAN_OR_RUN_CHANGED")
        active = ACTIVE | (ROUTING_SETUP if state.get("search_mode") == "source_routing" else set())
        if state["phase"] not in active:
            return result(
                state,
                "STAGE_FINISHED"
                if state["phase"] in {"COMPLETED", "PROPOSAL_READY"}
                else "ACTION_REQUIRED",
            )
        if polls >= max_polls or time.monotonic() >= stop:
            return result(state, "LOCAL_WAIT_BOUND_REACHED")
        preflight(root, state)
        if time.monotonic() >= stop:
            return result(current(), "LOCAL_WAIT_BOUND_REACHED")
        try:
            state = advance(root, ScopedControl())
        except WaitBoundReached:
            return result(current(), "LOCAL_WAIT_BOUND_REACHED")
        polls += 1
        if original_run is None:
            original_run = state.get("run_id")
        if emit is not None:
            # No arbitrary error, source text, URI, credential, label or output.
            emit(
                {
                    "event": "workflow_progress",
                    "polls": polls,
                    "local_worker_attempts": attempts,
                    "active": state["phase"] in active,
                    "approval_required": state["phase"] == "PLAN_READY",
                    "stage_finished": state["phase"] in {"COMPLETED", "PROPOSAL_READY"},
                }
            )
        if state.get("stop_code"):
            return result(state, "RECONCILIATION_REQUIRED")
        if local_worker and state["phase"] in {"QUEUED", "RUNNING"} and not attempted:
            if time.monotonic() >= stop:
                return result(state, "LOCAL_WAIT_BOUND_REACHED")
            attempted = True  # At most one invocation; a BLOCKED result is not retried.
            attempts += 1
            outcome = work_local(
                root,
                expected_identity=expected,
                expected_plan=original_plan,
                expected_plan_hash=original_hash,
                expected_run=original_run,
            )
            if outcome.get("status") not in {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED"}:
                return result(current(), "LOCAL_WORKER_STOPPED_INSPECT_CANONICAL_RUN")
            continue
        if state["phase"] not in active:
            continue
        if polls >= max_polls or time.monotonic() >= stop:
            continue
        time.sleep(min(poll_seconds, max(0, stop - time.monotonic())))


def work_local(
    root, *, expected_identity=None, expected_plan=None, expected_plan_hash=None, expected_run=None
):
    """Execute exactly one saved local run, after source and database scope checks."""
    import base64
    import os
    from decimal import Context, localcontext

    from sqlalchemy import select
    from sqlalchemy.exc import SQLAlchemyError

    from cutback.agent import load_session
    from cutback.evals.artifacts import EncryptedFileStore
    from cutback.local_stack import configured, database_url
    from cutback.server.db import ExperimentPlanRecord, ExperimentRunRecord, database
    from cutback.server.execution_ledger import recover_execution_leases
    from cutback.server.worker import run_one

    state = load_session(root)
    if expected_identity is not None and (
        identity(state) != expected_identity
        or state.get("plan", {}).get("id") != expected_plan
        or state.get("plan", {}).get("plan_hash") != expected_plan_hash
        or state.get("run_id") != expected_run
    ):
        raise ValueError("WORKFLOW_OPERATOR_SAVED_SCOPE_CHANGED")
    if state["phase"] not in {"QUEUED", "RUNNING"}:
        raise ValueError("APPROVED_RUN_REQUIRED")
    preflight(root, state)
    location, config = configured(root)
    if state["endpoint"] != config["endpoint"] or not config["allow_payloads"]:
        raise ValueError("LOCAL_OPERATOR_SCOPE_MISMATCH")
    try:
        engine, sessions = database(database_url(location, config))
    except SQLAlchemyError:
        raise ValueError("LOCAL_OPERATOR_DATABASE_UNAVAILABLE_INSPECT_OWNED_STACK") from None
    old = os.environ.get("CUTBACK_ALLOW_PAYLOADS")
    try:
        with sessions() as session:
            row = session.scalar(
                select(ExperimentRunRecord).where(
                    ExperimentRunRecord.id == UUID(state["run_id"]),
                    ExperimentRunRecord.project_id == UUID(state["project_id"]),
                    ExperimentRunRecord.plan_id == UUID(state["plan"]["id"]),
                )
            )
            if row is None:
                raise ValueError("LOCAL_OPERATOR_RUN_SCOPE_MISMATCH")
            plan = session.get(ExperimentPlanRecord, row.plan_id)
            if plan.specification.get("plan_hash") != state["plan"]["plan_hash"]:
                raise ValueError("LOCAL_OPERATOR_CANONICAL_PLAN_HASH_CHANGED")
        # Scope first, then metadata-only recovery before constructing a client
        # or checking provider keys. An uncertain crashed call cannot be retried.
        recover_execution_leases(sessions, only_run_id=UUID(state["run_id"]))
        with sessions() as session:
            row = session.get(ExperimentRunRecord, UUID(state["run_id"]))
            if row.status not in {"QUEUED", "BLOCKED"}:
                return {"status": row.status, "run_id": str(row.id)}
        os.environ["CUTBACK_ALLOW_PAYLOADS"] = "true"
        store = EncryptedFileStore(location / "encrypted", base64.b64decode(config["payload_key"]))
        with localcontext(Context(prec=80)):
            return run_one(
                sessions, store, execution_enabled=True, only_run_id=UUID(state["run_id"])
            )
    except SQLAlchemyError:
        raise ValueError("LOCAL_OPERATOR_DATABASE_UNAVAILABLE_INSPECT_OWNED_STACK") from None
    finally:
        engine.dispose()
        if old is None:
            os.environ.pop("CUTBACK_ALLOW_PAYLOADS", None)
        else:
            os.environ["CUTBACK_ALLOW_PAYLOADS"] = old
