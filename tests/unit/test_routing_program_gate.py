"""Routing confirmation never widens the ordinary model-edit acceptance boundary."""

from copy import deepcopy
from uuid import uuid4

import pytest
from test_routing_program_budget import program

from cutback.core.input_routing import LearnedRoutingBinding
from cutback.evals.datasets import digest
from cutback.evals.experiments import ExperimentPlan, ModelSpec, RouteSpec
from cutback.evals.routing_programs import program_confirmation_gate
from cutback.evals.search import regression_gate


def confirmation():
    specification, _ = program()
    source = specification.template.routing_execution.source
    binding = LearnedRoutingBinding(
        fit_id=uuid4(), fit_sha256="a" * 64, training_run_id=uuid4(), source=source
    )
    raw = specification.template.model_dump(mode="json")
    candidate = ModelSpec(
        key="frozen-router",
        provider="router",
        model="input-size-v1",
        routes=[
            RouteSpec(
                max_input_bytes=source.max_input_bytes, target=specification.template.candidates[0]
            )
        ],
    )
    raw.update(
        split="test",
        predecessor_run_id=str(uuid4()),
        candidates=[candidate.model_dump(mode="json")],
        routing_execution=binding.model_dump(mode="json"),
    )
    plan = ExperimentPlan.model_validate(raw)
    report = {
        "status": "COMPLETED",
        "billing_complete": True,
        "plan": {"plan_hash": digest(plan.model_dump(mode="json")), "split": "test"},
        "lineage": {"fresh_test_claim": True},
        "routing_source_current": True,
        "routing_fit_binding": binding.model_dump(mode="json"),
        "recommendations": [
            {
                "candidate": {"key": candidate.key},
                "evaluator_version": "deterministic-v16",
                "quality_gate_passed": True,
                "cheaper_on_sample": True,
                "evidence_tier": 4,
                "statistical_gate_passed": True,
                "status": "VERIFIED_ON_HELDOUT_DATASET",
            }
        ],
    }
    return plan, report


def test_routing_gate_is_separate_from_ordinary_model_change_gate():
    plan, report = confirmation()
    assert program_confirmation_gate(report, plan)["passed"] is True
    assert (
        regression_gate(report, expected_plan_hash=report["plan"]["plan_hash"])["passed"] is False
    )
    report["synthetic_execution"] = {"status": "synthetic"}
    assert program_confirmation_gate(report, plan)["passed"] is False
    assert program_confirmation_gate(report, plan, sample_only=True)["passed"] is True


@pytest.mark.parametrize(
    "mutation",
    ["lineage", "fit", "fit_binding", "billing", "plan", "quality", "price", "statistical"],
)
def test_current_source_routing_gates_abstain_on_missing_or_changed_evidence(mutation):
    plan, original = confirmation()
    report = deepcopy(original)
    if mutation == "lineage":
        report["lineage"]["fresh_test_claim"] = False
    elif mutation == "fit":
        report["routing_source_current"] = False
    elif mutation == "fit_binding":
        report["routing_fit_binding"]["fit_id"] = str(uuid4())
    elif mutation == "billing":
        report["billing_complete"] = False
    elif mutation == "plan":
        report["plan"]["plan_hash"] = "f" * 64
    elif mutation == "quality":
        report["recommendations"][0]["quality_gate_passed"] = False
    elif mutation == "price":
        report["recommendations"][0]["cheaper_on_sample"] = False
    else:
        report["recommendations"][0]["statistical_gate_passed"] = False
    assert program_confirmation_gate(report, plan)["passed"] is False
