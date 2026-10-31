"""Durable independent confirmation and exact source review for joint retrieval."""

from uuid import uuid4

from cutback.agent_retrieval import selected_source
from cutback.core.workspace import workspace_lock
from cutback.evals.experiments import Budget, ExperimentPlan, plan_summary
from cutback.evals.joint_retrieval_planning import confirmation_plan
from cutback.evals.retrieval_joint import JOINT_RETRIEVAL_CONTRACT


def confirm(root, control, *, candidate, max_usd, max_requests, purpose):
    from cutback.agent import load_session, public_session, save_session, workspace

    budget = Budget(max_usd=max_usd, max_requests=max_requests)
    root, directory = workspace(root)
    with workspace_lock(directory):
        state = load_session(root)
        if (
            state.get("search_mode") != "joint_model_retrieval"
            or state.get("endpoint") != control.endpoint
        ):
            raise ValueError("JOINT_RETRIEVAL_COLLECTOR_OR_MODE_MISMATCH")
        source = selected_source(root, state, state["selected_workflow"])
        frozen = state.get("confirmation")
        if frozen:
            if candidate != frozen["selected_candidate"] or budget != Budget.model_validate(
                frozen["budget"]
            ):
                raise ValueError("FROZEN_CONFIRMATION_INTENT_CHANGED")
            if state["phase"] == "PLAN_READY" and state["plan"]["id"] == frozen["request_id"]:
                return public_session(state)
            if state["phase"] != "PREPARING_CONFIRMATION":
                raise ValueError("COMPLETED_SEARCH_OR_SAME_CONFIRMATION_PREPARATION_REQUIRED")
        elif state["phase"] != "COMPLETED":
            raise ValueError("COMPLETED_JOINT_RETRIEVAL_SEARCH_REQUIRED")
        ready = control("GET", "/v1/health/ready")
        if (
            ready.get("joint_retrieval_contract") != JOINT_RETRIEVAL_CONTRACT
            or ready.get("retrieval_recipe_sha256")
            != source["configuration_binding"]["retrieval_contract_sha256"]
        ):
            raise ValueError("COLLECTOR_JOINT_RETRIEVAL_UPGRADE_REQUIRED")
        base = f"/v1/projects/{state['project_id']}"
        search_plan_id = frozen["search_plan_id"] if frozen else state["plan"]["id"]
        search_plan_hash = frozen["search_plan_hash"] if frozen else state["plan"]["plan_hash"]
        search_run_id = frozen["search_run_id"] if frozen else state["run_id"]
        content = control(
            "POST", base + f"/experiments/plans/{search_plan_id}/content", {"purpose": purpose}
        )
        if content["plan_hash"] != search_plan_hash:
            raise ValueError("FROZEN_SEARCH_PLAN_CHANGED")
        prior = ExperimentPlan.model_validate(content["specification"])
        canonical = control("GET", base + f"/experiments/{search_run_id}")
        if (
            canonical.get("id") != search_run_id
            or canonical.get("results", {}).get("plan", {}).get("plan_hash") != search_plan_hash
        ):
            raise ValueError("JOINT_RETRIEVAL_VALIDATION_REPORT_IDENTITY_CHANGED")
        specification = confirmation_plan(
            prior, canonical["results"], search_run_id, budget, candidate=candidate
        )
        summary = plan_summary(
            specification,
            {
                "immutable_hash": specification.dataset_hash,
                "items": [{"split": "test"}] * state["dataset_split_counts"].get("test", 0),
            },
        )
        if not summary["fits_budget"]:
            # No intent or POST was saved, so a rejected budget can be corrected safely.
            raise ValueError("CONFIRMATION_EXCEEDS_BUDGET_OR_HAS_NO_TEST_GROUPS")
        if not frozen:
            frozen = {
                "search_run_id": search_run_id,
                "search_plan_id": search_plan_id,
                "search_plan_hash": search_plan_hash,
                "selected_candidate": candidate,
                "request_id": str(uuid4()),
                "budget": budget.model_dump(mode="json"),
            }
            state["confirmation"] = frozen
        save_session(directory, state, "PREPARING_CONFIRMATION")
        planned = control(
            "POST",
            base + "/experiments/plans",
            {
                "request_id": frozen["request_id"],
                "dataset_id": state["dataset_id"],
                "specification": specification.model_dump(mode="json"),
            },
        )
        if planned["id"] != frozen["request_id"]:
            raise ValueError("CONFIRMATION_PREPARATION_IDENTITY_CHANGED")
        if planned["plan_hash"] != summary["plan_hash"]:
            raise ValueError("CONFIRMATION_PREPARATION_PLAN_CHANGED")
        state["plan"] = planned
        for name in ("run_id", "report", "decisions", "search_results", "proposal"):
            state.pop(name, None)
        save_session(directory, state, "PLAN_READY")
        return public_session(state)


def proposal(root, control, *, purpose, candidate=None):
    from cutback.agent import load_session, save_session, workspace
    from cutback.joint_retrieval_changes import JointRetrievalProposal, prepare_patch

    root, directory = workspace(root)
    with workspace_lock(directory):
        state = load_session(root)
        if (
            state.get("endpoint") != control.endpoint
            or state.get("search_mode") != "joint_model_retrieval"
        ):
            raise ValueError("JOINT_RETRIEVAL_COLLECTOR_OR_MODE_MISMATCH")
        if state["phase"] not in {"COMPLETED", "PROPOSAL_READY"}:
            raise ValueError("COMPLETED_RUN_REQUIRED")
        selected_source(root, state, state["selected_workflow"])
        selected = state.get("confirmation", {}).get("selected_candidate")
        if not selected or (candidate is not None and candidate != selected):
            raise ValueError("EXACT_JOINT_RETRIEVAL_CONFIRMATION_CANDIDATE_REQUIRED")
        value = control(
            "POST",
            f"/v1/projects/{state['project_id']}/model-retrieval/{state['run_id']}/proposal",
            {"purpose": purpose},
        )
        proposed = JointRetrievalProposal.model_validate(value)
        if (
            str(proposed.run_id) != state["run_id"]
            or proposed.plan_hash != state["plan"]["plan_hash"]
            or proposed.proposed.key != selected
        ):
            raise ValueError("JOINT_RETRIEVAL_CONFIRMATION_PROPOSAL_IDENTITY_MISMATCH")
        artifact = prepare_patch(root, proposed)
        state["proposal"] = {
            "version": artifact["version"],
            "run_id": str(proposed.run_id),
            "proposed_files": artifact["proposed_files"],
        }
        save_session(directory, state, "PROPOSAL_READY")
        return artifact
