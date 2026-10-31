"""Durable scenario intent, one-call reconciliation and a separate owner-review boundary."""

from pathlib import Path
from uuid import UUID, uuid4

from cutback.core.request_profiles import fingerprint
from cutback.core.workspace import private_directory, read_private, workspace_lock, write_private


def location(root):
    root = Path(root).resolve(strict=True)
    from cutback.agent import workspace

    workspace(root)
    return root, private_directory(root, ".cutback/scenario-agent")


def load(root, control, project):
    root, directory = location(root)
    state = read_private(directory / "session.json")
    if (
        state["root_fingerprint"] != fingerprint(str(root))
        or state["endpoint"] != control.endpoint
        or state["project_id"] != str(UUID(str(project)))
    ):
        raise ValueError("SCENARIO_WORKFLOW_DESTINATION_OR_ROOT_CHANGED")
    return directory, state


def public(state):
    phase = state["phase"]
    return {
        **state,
        "next_action": {
            "code": {
                "PREPARING": "RESUME_SAME_PREPARATION",
                "PLANNED": "REVIEW_EXACT_ONE_CALL_BUDGET",
                "APPROVING": "RECONCILE_APPROVAL",
                "QUEUEING": "RECONCILE_SAME_RUN_ONLY",
                "QUEUED": "POLL_SAME_RUN",
                "RUNNING": "POLL_SAME_RUN",
                "REVIEW_REQUIRED": "READ_PRIVATE_PROPOSAL_AND_REQUEST_OWNER_REVIEW",
                "REVIEWED": "IMPORT_EXACT_REVIEWED_DEVELOPMENT_VERSION",
                "IMPORTED": "PREPARE_SEPARATELY_APPROVED_DEVELOPMENT_VALIDATION",
                "STOPPED": "INSPECT_FAILURE_WITHOUT_REPLAY",
            }.get(phase, "INSPECT_CURRENT_STATE"),
            "automatic_billable_retry": False,
            "generated_cases_are_test_evidence": False,
        },
    }


def prepare(root, control, project, request):
    from cutback.evals.scenario_generation import ScenarioPreparation, generator_model

    root, directory = location(root)
    project = str(UUID(str(project)))
    request = ScenarioPreparation.model_validate(request)
    generator_model(request.generator)
    body = request.model_dump(mode="json", exclude={"request_id"})
    sha = fingerprint(body)
    with workspace_lock(directory):
        path = directory / "session.json"
        if path.exists():
            _, state = load(root, control, project)
            if (
                state["request_sha256"] != sha
                or request.request_id
                and str(request.request_id) != state["scenario_id"]
            ):
                raise ValueError("SCENARIO_WORKFLOW_ORIGINAL_REQUEST_REQUIRED")
        else:
            state = {
                "root_fingerprint": fingerprint(str(root)),
                "endpoint": control.endpoint,
                "project_id": project,
                "scenario_id": str(request.request_id or uuid4()),
                "request_sha256": sha,
                "phase": "PREPARING",
            }
            write_private(path, state)
        if state["phase"] == "PREPARING":
            row = control(
                "POST",
                f"/v1/projects/{project}/scenarios/drafts",
                {**body, "request_id": state["scenario_id"]},
            )
            if row["id"] != state["scenario_id"]:
                raise ValueError("SCENARIO_WORKFLOW_PREPARATION_IDENTITY_CHANGED")
            state.update(phase="PLANNED", generation=row)
            write_private(path, state)
        return public(state)


def advance(root, control, project, *, plan_hash=None, approval_ref=None):
    from pydantic import TypeAdapter

    from cutback.core.models import Identifier

    root, directory = location(root)
    with workspace_lock(directory):
        _, state = load(root, control, project)
        path = f"/v1/projects/{state['project_id']}/scenarios/{state['scenario_id']}"
        row = control("GET", path)
        expected = state["generation"]["plan_hash"]
        if row["plan_hash"] != expected:
            raise ValueError("SCENARIO_WORKFLOW_FROZEN_PLAN_CHANGED")
        if state["phase"] in {"REVIEWING", "REVIEWED", "IMPORTED", "STOPPED"}:
            state["generation"] = row
            write_private(directory / "session.json", state)
            return public(state)
        if state["phase"] == "PLANNED":
            if plan_hash != expected or not approval_ref:
                raise ValueError("SCENARIO_WORKFLOW_EXACT_BUDGET_APPROVAL_REQUIRED")
            TypeAdapter(Identifier).validate_python(approval_ref)
            state["phase"] = "APPROVING"
            write_private(directory / "session.json", state)
            row = control(
                "POST",
                path + "/approve-generation",
                {"plan_hash": expected, "approval_ref": approval_ref},
            )
        if state["phase"] == "APPROVING":
            if row.get("generation_run_id"):
                state["phase"] = "QUEUED"
            elif row["generation_approval_status"] == "approved":
                state["phase"] = "QUEUEING"
                write_private(directory / "session.json", state)
                row = control("POST", path + "/runs", {"plan_hash": expected})
            else:
                state["stop_code"] = "SCENARIO_APPROVAL_UNCERTAIN_RECONCILE_CURRENT_PLAN"
        # After an uncertain queue reply, only GET is permitted. No new dispatch intent.
        if state["phase"] == "QUEUEING" and not row.get("generation_run_id"):
            row = control("GET", path)
            if not row.get("generation_run_id"):
                state["stop_code"] = "SCENARIO_QUEUE_UNCERTAIN_RECONCILE_CURRENT_RUN"
        if row.get("generation_run_id"):
            state["phase"] = (
                "REVIEW_REQUIRED"
                if row["state"] == "COMPLETED"
                else row["state"]
                if row["state"] in {"QUEUED", "RUNNING"}
                else "STOPPED"
            )
            state.pop("stop_code", None)
        state["generation"] = row
        write_private(directory / "session.json", state)
        return public(state)


def read(root, control, project, *, purpose):
    root, directory = location(root)
    with workspace_lock(directory):
        _, state = load(root, control, project)
        if state["phase"] != "REVIEW_REQUIRED":
            raise ValueError("SCENARIO_COMPLETE_GENERATION_REQUIRED_BEFORE_REVIEW")
        content = control(
            "POST",
            f"/v1/projects/{state['project_id']}/scenarios/{state['scenario_id']}/content",
            {"purpose": purpose},
        )
        if (
            content["state"] != "COMPLETED"
            or content["version_sha256"] != state["generation"]["version_sha256"]
        ):
            raise ValueError("SCENARIO_CURRENT_READ_VERSION_CHANGED")
        state["read_version_sha256"] = content["version_sha256"]
        state["read_review_approval_id"] = content["review_approval_id"]
        write_private(directory / "session.json", state)
        return content


def review_and_import(root, control, project, *, version_sha256, decision, approval_ref):
    from pydantic import TypeAdapter

    from cutback.core.models import Identifier

    TypeAdapter(Identifier).validate_python(approval_ref)
    if decision not in {"approve", "reject"}:
        raise ValueError("SCENARIO_EXPLICIT_OWNER_REVIEW_DECISION_REQUIRED")
    root, directory = location(root)
    with workspace_lock(directory):
        _, state = load(root, control, project)
        if state["phase"] not in {
            "REVIEW_REQUIRED",
            "REVIEWING",
            "REVIEWED",
            "IMPORTED",
        } or version_sha256 != state.get("read_version_sha256"):
            raise ValueError("SCENARIO_EXACT_PURPOSE_READ_VERSION_REQUIRED")
        if state.get("review_intent") and state["review_intent"] != {
            "decision": decision,
            "approval_ref": approval_ref,
        }:
            raise ValueError("SCENARIO_WORKFLOW_REVIEW_INTENT_CHANGED")
        path = f"/v1/projects/{state['project_id']}/scenarios/{state['scenario_id']}"
        if state["phase"] in {"REVIEW_REQUIRED", "REVIEWING"}:
            state.update(
                phase="REVIEWING",
                review_intent={"decision": decision, "approval_ref": approval_ref},
            )
            write_private(directory / "session.json", state)
            row = control(
                "POST",
                path + "/review",
                {
                    "version_sha256": version_sha256,
                    "expected_review_approval_id": state.get("read_review_approval_id"),
                    "decision": decision,
                    "approval_ref": approval_ref,
                },
            )
            state["generation"] = row
            state["phase"] = "REVIEWED" if decision == "approve" else "STOPPED"
            state["import_request_id"] = str(uuid4())
            write_private(directory / "session.json", state)
        if state["phase"] in {"REVIEWED", "IMPORTED"}:
            # Stable nonbillable identity; every replay rechecks the exact current review.
            state["development_dataset"] = control(
                "POST",
                path + "/imports",
                {
                    "request_id": state["import_request_id"],
                    "version_sha256": version_sha256,
                    "review_approval_id": state["generation"]["review_approval_id"],
                    "approval_ref": approval_ref,
                },
            )
            state["phase"] = "IMPORTED"
            write_private(directory / "session.json", state)
        return public(state)
