"""Durable agent criteria preparation; private content is never copied into session metadata."""

from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

from cutback.core.models import StrictModel
from cutback.core.request_profiles import fingerprint
from cutback.core.workspace import read_private, workspace_lock, write_private
from cutback.evals.criteria_generation import CriteriaRequirements, generator_model
from cutback.evals.experiments import Budget, ExperimentPlan, ModelSpec, upper_cost


class CriteriaPreparation(StrictModel):
    requirements: CriteriaRequirements
    generator: ModelSpec
    budget: Budget
    max_training_examples: int = Field(default=4, strict=True, ge=1, le=8)
    transport: Literal["native_provider", "scripted_test_only"] = "native_provider"


def _basis(root, control, state, purpose):

    base = f"/v1/projects/{state['project_id']}"
    content = control(
        "POST",
        base + f"/experiments/plans/{state['criteria']['basis_plan_id']}/content",
        {"purpose": purpose},
    )
    if content["plan_hash"] != state["criteria"]["basis_plan_hash"]:
        raise ValueError("CRITERIA_SOURCE_BASIS_CHANGED")
    plan = ExperimentPlan.model_validate(content["specification"])
    if plan.source_binding != _scope(root, control, state).binding:
        raise ValueError("CRITERIA_SOURCE_BASIS_CHANGED")
    execution = control(
        "GET", base + f"/experiments/plans/{state['criteria']['basis_plan_id']}/execution"
    )
    if execution.get("run_id"):
        raise ValueError("CRITERIA_SOURCE_BASIS_ALREADY_EXECUTED")
    if (
        plan.split != "validation"
        or plan.predecessor_run_id
        or plan.criteria_binding
        or plan.episode_execution
        or plan.retrieval_execution
        or plan.compression_execution
        or plan.judge
        or plan.staged
        or plan.prompt_bindings
    ):
        raise ValueError("CRITERIA_REQUIRES_UNEXECUTED_NATIVE_SEARCH_BASIS")
    return plan


def _scope(root, control, state):
    from cutback.agent import selected_profile

    if state.get("endpoint") != control.endpoint:
        raise ValueError("COLLECTOR_DESTINATION_MISMATCH")
    return selected_profile(root, state, state["selected_workflow"])


def prepare(root, control, request, *, purpose):
    from cutback.agent import load_session, public_session, save_session, workspace

    request = CriteriaPreparation.model_validate(request)
    model = generator_model(request.generator)
    if (
        request.transport not in {"native_provider", "scripted_test_only"}
        or request.budget.max_requests != 1
        or upper_cost(model) > request.budget.max_usd
    ):
        raise ValueError("CRITERIA_ONE_CALL_GENERATION_BUDGET_REQUIRED")
    root, directory = workspace(root)
    with workspace_lock(directory):
        state = load_session(root)
        _scope(root, control, state)
        if state.get("search_mode") == "reviewed_criteria":
            raise ValueError("CRITERIA_ALREADY_IN_VALIDATION")
        if "criteria" not in state:
            if (
                state["phase"] != "PLAN_READY"
                or state.get("confirmation")
                or state.get("search_mode") != "model_and_output_cap"
            ):
                raise ValueError("CRITERIA_PREPARATION_REQUIRES_UNEXECUTED_NATIVE_SEARCH")
            state["criteria"] = {
                "id": str(uuid4()),
                "basis_plan_id": state["plan"]["id"],
                "basis_plan_hash": state["plan"]["plan_hash"],
                "request_sha256": fingerprint(request.model_dump(mode="json")),
            }
        if state["criteria"]["request_sha256"] != fingerprint(request.model_dump(mode="json")):
            raise ValueError("CRITERIA_GENERATION_INTENT_CHANGED_START_NEW_SESSION")
        if state["phase"] not in {"PLAN_READY", "PREPARING_CRITERIA", "CRITERIA_PLAN_READY"}:
            raise ValueError("CRITERIA_GENERATION_ALREADY_STARTED_INSPECT_STATUS")
        basis = _basis(root, control, state, purpose)
        requirements = request.requirements
        checks = [basis.metric, *(item.metric for item in basis.criteria if item.required)]
        if (
            requirements.minimum < max(item.minimum for item in checks)
            or requirements.max_regression > min(item.max_regression for item in checks)
            or requirements.min_test_samples < max(item.min_test_samples for item in checks)
            or requirements.min_test_groups < max(item.min_test_groups for item in checks)
        ):
            raise ValueError("CRITERIA_REQUIREMENTS_CANNOT_RELAX_PREPARED_BASIS")
        save_session(directory, state, "PREPARING_CRITERIA")
        result = control(
            "POST",
            f"/v1/projects/{state['project_id']}/criteria/drafts",
            {
                "request_id": state["criteria"]["id"],
                "dataset_id": state["dataset_id"],
                **request.model_dump(mode="json"),
            },
        )
        if result["id"] != state["criteria"]["id"]:
            raise ValueError("CRITERIA_GENERATION_IDENTITY_CHANGED")
        state["criteria"]["generation"] = result
        state["provider_calls"] = None if result["request_reservations"] else 0
        phase = "CRITERIA_PLAN_READY"
        if result.get("generation_run_id"):
            phase = (
                "CRITERIA_REVIEW_REQUIRED"
                if result["state"] == "COMPLETED"
                else "CRITERIA_RUNNING"
                if result["state"] == "RUNNING"
                else "CRITERIA_QUEUED"
                if result["state"] == "QUEUED"
                else "CRITERIA_STOPPED"
            )
        save_session(directory, state, phase)
        return public_session(state)


def advance(root, control, *, plan_hash=None, approval_ref=None):
    from cutback.agent import load_session, public_session, save_session, workspace

    root, directory = workspace(root)
    with workspace_lock(directory):
        state = load_session(root)
        _scope(root, control, state)
        path = f"/v1/projects/{state['project_id']}/criteria/{state['criteria']['id']}"
        expected = state["criteria"]["generation"]["plan_hash"]
        if plan_hash is not None and plan_hash != expected:
            raise ValueError("EXACT_CRITERIA_PLAN_HASH_REQUIRED")
        if state["phase"] == "CRITERIA_PLAN_READY":
            current = control("GET", path)
            if current["plan_hash"] != expected:
                raise ValueError("CRITERIA_GENERATION_PLAN_CHANGED")
            if current["generation_approval_status"] == "pending":
                if plan_hash != expected or not approval_ref:
                    raise ValueError("EXACT_CRITERIA_PLAN_HASH_AND_APPROVAL_REFERENCE_REQUIRED")
                state["provider_calls"] = None
                save_session(directory, state, "APPROVING_CRITERIA")
                control(
                    "POST",
                    path + "/approve-generation",
                    {"plan_hash": expected, "approval_ref": approval_ref},
                )
            save_session(directory, state, "QUEUEING_CRITERIA")
        if state["phase"] in {"APPROVING_CRITERIA", "QUEUEING_CRITERIA"}:
            current = control("GET", path)
            if current["plan_hash"] != expected:
                raise ValueError("CRITERIA_GENERATION_PLAN_CHANGED")
            if current.get("generation_run_id"):
                state["criteria"]["generation"] = current
                save_session(directory, state, "CRITERIA_QUEUED")
            elif current["generation_approval_status"] == "approved":
                save_session(directory, state, "QUEUEING_CRITERIA")
                state["criteria"]["generation"] = control(
                    "POST", path + "/runs", {"plan_hash": expected}
                )
                save_session(directory, state, "CRITERIA_QUEUED")
            else:
                raise ValueError("CRITERIA_APPROVAL_NOT_CONFIRMED_RECONCILE_STATUS")
        if state["phase"] in {"CRITERIA_QUEUED", "CRITERIA_RUNNING", "CRITERIA_REVIEW_REQUIRED"}:
            current = control("GET", path)
            state["criteria"]["generation"] = current
            state["provider_calls"] = None if current["request_reservations"] else 0
            phase = (
                "CRITERIA_REVIEW_REQUIRED"
                if current["state"] == "COMPLETED"
                else "CRITERIA_RUNNING"
                if current["state"] == "RUNNING"
                else "CRITERIA_QUEUED"
                if current["state"] in {"QUEUED", "PENDING"}
                else "CRITERIA_STOPPED"
            )
            save_session(directory, state, phase, current.get("code"))
        return public_session(state)


def read(root, control, *, purpose):
    from cutback.agent import load_session, save_session, workspace

    root, directory = workspace(root)
    with workspace_lock(directory):
        state = load_session(root)
        _scope(root, control, state)
        if state["phase"] != "CRITERIA_REVIEW_REQUIRED":
            raise ValueError("COMPLETE_CRITERIA_GENERATION_REQUIRED")
        basis = _basis(root, control, state, purpose)
        content = control(
            "POST",
            f"/v1/projects/{state['project_id']}/criteria/{state['criteria']['id']}/content",
            {"purpose": purpose},
        )
        state["criteria"]["read_version_sha256"] = content["version_sha256"]
        state["criteria"]["read_review_approval_id"] = content["review_approval_id"]
        save_session(directory, state)
        return {
            "basis_metric": basis.metric.model_dump(mode="json"),
            "basis_required_criteria": [
                item.model_dump(mode="json") for item in basis.criteria if item.required
            ],
            **content,
        }


def review(root, control, *, version_sha256, decision, approval_ref, purpose):
    from cutback.agent import load_session, public_session, save_session, workspace

    if decision not in {"approve", "reject"}:
        raise ValueError("CRITERIA_REVIEW_DECISION_REQUIRED")
    root, directory = workspace(root)
    with workspace_lock(directory):
        state = load_session(root)
        _scope(root, control, state)
        if state["phase"] not in {
            "CRITERIA_REVIEW_REQUIRED",
            "REVIEWING_CRITERIA",
            "PREPARING_REVIEWED_VALIDATION",
        } or version_sha256 != state["criteria"].get("read_version_sha256"):
            raise ValueError("EXACT_PURPOSE_READ_CRITERIA_VERSION_REQUIRED")
        path = f"/v1/projects/{state['project_id']}/criteria/{state['criteria']['id']}"
        if state["phase"] != "PREPARING_REVIEWED_VALIDATION":
            if state["phase"] == "REVIEWING_CRITERIA" and state["criteria"]["decision"] != decision:
                raise ValueError("CRITERIA_REVIEW_INTENT_CHANGED_RECONCILE_STATUS")
            state["criteria"]["decision"] = decision
            save_session(directory, state, "REVIEWING_CRITERIA")
            current = control(
                "POST",
                path + "/review",
                {
                    "version_sha256": version_sha256,
                    "decision": decision,
                    "approval_ref": approval_ref,
                    "expected_review_approval_id": state["criteria"].get("read_review_approval_id"),
                },
            )
            state["criteria"]["generation"] = current
            if decision == "reject":
                save_session(directory, state, "CRITERIA_REJECTED")
                return public_session(state)
            key = directory / ("criteria-key-" + str(UUID(state["id"])) + ".json")
            write_private(key, {"request_id": str(uuid4()), "content_nonce": str(uuid4())})
            save_session(directory, state, "PREPARING_REVIEWED_VALIDATION")
        if decision != "approve":
            raise ValueError("CRITERIA_REVIEW_INTENT_CHANGED_RECONCILE_STATUS")
        basis = _basis(root, control, state, purpose)
        current = state["criteria"]["generation"]
        key = read_private(directory / ("criteria-key-" + str(UUID(state["id"])) + ".json"))
        plan = control(
            "POST",
            path + "/evaluation-draft",
            {
                **key,
                "dataset_id": state["dataset_id"],
                "criteria_binding": {
                    "criteria_id": current["id"],
                    "review_approval_id": current["review_approval_id"],
                    "version_sha256": current["version_sha256"],
                },
                "baseline": basis.baseline.model_dump(mode="json"),
                "candidates": [item.model_dump(mode="json") for item in basis.candidates],
                "budget": basis.budget.model_dump(mode="json"),
                "source_binding": basis.source_binding.model_dump(mode="json")
                if basis.source_binding
                else None,
                "retain_case_outputs": basis.retain_case_outputs,
                "repeats": basis.repeats,
            },
        )
        state["plan"] = plan
        state["search_mode"] = "reviewed_criteria"
        save_session(directory, state, "PLAN_READY")
        return public_session(state)


def confirm(root, control, *, candidate, max_usd, max_requests, purpose):
    """Freeze or reconcile the same nonbillable confirmation preparation."""
    from cutback.agent import load_session, public_session, save_session, workspace

    budget = Budget.model_validate({"max_usd": max_usd, "max_requests": max_requests})
    root, directory = workspace(root)
    with workspace_lock(directory):
        state = load_session(root)
        _scope(root, control, state)
        if state.get("search_mode") != "reviewed_criteria":
            raise ValueError("REVIEWED_CRITERIA_SEARCH_REQUIRED")
        if state["phase"] == "COMPLETED" and not state.get("confirmation"):
            decision: dict = next(
                (row for row in state.get("decisions", []) if row["candidate"]["key"] == candidate),
                {},
            )
            if not decision.get("quality_gate_passed") or not decision.get("cheaper_on_sample"):
                raise ValueError("SELECTED_SEARCH_CANDIDATE_DID_NOT_PASS_GATES")
            state["confirmation"] = {
                "search_run_id": state["run_id"],
                "search_plan_id": state["plan"]["id"],
                "search_plan_hash": state["plan"]["plan_hash"],
                "selected_candidate": candidate,
                "request_id": str(uuid4()),
                "budget": budget.model_dump(mode="json"),
            }
            save_session(directory, state, "PREPARING_CONFIRMATION")
        if state["phase"] != "PREPARING_CONFIRMATION" or not state.get("confirmation"):
            raise ValueError("COMPLETED_SEARCH_OR_SAME_CONFIRMATION_PREPARATION_REQUIRED")
        frozen = state["confirmation"]
        if candidate != frozen["selected_candidate"] or budget != Budget.model_validate(
            frozen["budget"]
        ):
            raise ValueError("FROZEN_CONFIRMATION_INTENT_CHANGED")
        base = f"/v1/projects/{state['project_id']}"
        content = control(
            "POST",
            base + f"/experiments/plans/{frozen['search_plan_id']}/content",
            {"purpose": purpose},
        )
        if content["plan_hash"] != frozen["search_plan_hash"]:
            raise ValueError("FROZEN_SEARCH_PLAN_CHANGED")
        prior = ExperimentPlan.model_validate(content["specification"])
        selected = [item for item in prior.candidates if item.key == candidate]
        if (
            prior.split != "validation"
            or prior.predecessor_run_id
            or len(selected) != 1
            or prior.criteria_binding is None
            or str(prior.criteria_binding.criteria_id) != state["criteria"]["id"]
        ):
            raise ValueError("EXACT_REVIEWED_VALIDATION_CANDIDATE_REQUIRED")
        raw = prior.model_dump(mode="json")
        raw.update(
            name="Reviewed criteria confirmation",
            split="test",
            sample_limit=None,
            candidates=[selected[0].model_dump(mode="json")],
            predecessor_run_id=frozen["search_run_id"],
            budget=budget.model_dump(mode="json"),
        )
        plan = ExperimentPlan.model_validate(raw)
        planned = control(
            "POST",
            base + "/experiments/plans",
            {
                "request_id": frozen["request_id"],
                "dataset_id": state["dataset_id"],
                "specification": plan.model_dump(mode="json"),
            },
        )
        if planned["id"] != frozen["request_id"]:
            raise ValueError("CONFIRMATION_PREPARATION_IDENTITY_CHANGED")
        state["plan"] = planned
        for key in ("run_id", "report", "decisions", "search_results", "proposal"):
            state.pop(key, None)
        save_session(directory, state, "PLAN_READY")
        return public_session(state)
