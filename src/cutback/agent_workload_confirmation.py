"""Reconcile one independently approved native portfolio confirmation without replay."""

from uuid import uuid4

from cutback.core.workload_portfolio import WORKLOAD_PORTFOLIO_CONTRACT
from cutback.core.workspace import workspace_lock
from cutback.evals.experiments import Budget, ExperimentPlan, plan_summary


def confirm(root, control, *, candidate, max_usd, max_requests, purpose):
    from cutback.agent import (
        load_session,
        public_session,
        save_session,
        selected_profile,
        workspace,
    )

    budget = Budget(max_usd=max_usd, max_requests=max_requests)
    root, directory = workspace(root)
    with workspace_lock(directory):
        state = load_session(root)
        if (
            state.get("search_mode") != "model_and_output_cap"
            or state.get("endpoint") != control.endpoint
        ):
            raise ValueError("WORKLOAD_PORTFOLIO_COLLECTOR_OR_MODE_CHANGED")
        selected_profile(root, state, state["selected_workflow"])
        frozen = state.get("confirmation")
        if frozen:
            if candidate != frozen["selected_candidate"] or budget != Budget.model_validate(
                frozen["budget"]
            ):
                raise ValueError("FROZEN_CONFIRMATION_INTENT_CHANGED")
            if state["phase"] == "PLAN_READY" and state["plan"]["id"] == frozen["request_id"]:
                return public_session(state)
            if state["phase"] != "PREPARING_CONFIRMATION":
                raise ValueError("SAME_WORKLOAD_CONFIRMATION_PREPARATION_REQUIRED")
        elif state["phase"] != "COMPLETED":
            raise ValueError("COMPLETED_WORKLOAD_PORTFOLIO_SEARCH_REQUIRED")
        if (
            control("GET", "/v1/health/ready").get("workload_portfolio_contract")
            != WORKLOAD_PORTFOLIO_CONTRACT
        ):
            raise ValueError("COLLECTOR_WORKLOAD_PORTFOLIO_UPGRADE_REQUIRED")
        base = f"/v1/projects/{state['project_id']}"
        search_plan_id = frozen["search_plan_id"] if frozen else state["plan"]["id"]
        search_hash = frozen["search_plan_hash"] if frozen else state["plan"]["plan_hash"]
        search_run_id = frozen["search_run_id"] if frozen else state["run_id"]
        content = control(
            "POST", base + f"/experiments/plans/{search_plan_id}/content", {"purpose": purpose}
        )
        if content["plan_hash"] != search_hash:
            raise ValueError("FROZEN_SEARCH_PLAN_CHANGED")
        prior = ExperimentPlan.model_validate(content["specification"])
        report = control("GET", base + f"/experiments/{search_run_id}")
        if (
            report.get("id") != search_run_id
            or report.get("status") != "COMPLETED"
            or report.get("results", {}).get("plan", {}).get("plan_hash") != search_hash
            or report.get("results", {}).get("billing_complete") is not True
            or prior.workload_portfolio is None
            or prior.evaluator_version != "deterministic-v3"
            or prior.split != "validation"
            or prior.predecessor_run_id
        ):
            raise ValueError("CANONICAL_COMPLETED_WORKLOAD_SEARCH_REQUIRED")
        decision: dict = next(
            (
                row
                for row in report["results"].get("recommendations", [])
                if row["candidate"]["key"] == candidate
            ),
            {},
        )
        matches = [value for value in prior.candidates if value.key == candidate]
        if (
            len(matches) != 1
            or decision.get("quality_gate_passed") is not True
            or decision.get("cheaper_on_sample") is not True
        ):
            raise ValueError("SELECTED_WORKLOAD_CANDIDATE_DID_NOT_PASS_GATES")
        raw = prior.model_dump(mode="json")
        raw.update(
            candidates=[matches[0].model_dump(mode="json")],
            split="test",
            predecessor_run_id=search_run_id,
            sample_limit=None,
            budget=budget.model_dump(mode="json"),
        )
        specification = ExperimentPlan.model_validate(raw)
        summary = plan_summary(
            specification,
            {
                "immutable_hash": specification.dataset_hash,
                "items": [{"split": "test"}] * state["dataset_split_counts"].get("test", 0),
            },
        )
        if not summary["fits_budget"]:
            raise ValueError("CONFIRMATION_EXCEEDS_BUDGET_OR_HAS_NO_TEST_GROUPS")
        if not frozen:
            frozen = {
                "search_run_id": search_run_id,
                "search_plan_id": search_plan_id,
                "search_plan_hash": search_hash,
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
        if planned["id"] != frozen["request_id"] or planned["plan_hash"] != summary["plan_hash"]:
            raise ValueError("WORKLOAD_CONFIRMATION_PREPARATION_IDENTITY_CHANGED")
        state["plan"] = planned
        for name in ("run_id", "report", "decisions", "search_results", "proposal"):
            state.pop(name, None)
        save_session(directory, state, "PLAN_READY")
        return public_session(state)
