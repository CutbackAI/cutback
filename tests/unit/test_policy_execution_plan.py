"""Distinct plan scopes, private projection and no fallback for an unfinished adapter."""

from decimal import Decimal, localcontext
from uuid import uuid4

import pytest
from test_policy_execution_artifacts import artifacts

from cutback.evals.experiments import ExperimentPlan, plan_summary, run, upper_cost
from cutback.evals.policy_artifacts import decode_policy_bundle
from cutback.evals.policy_models import policy_descriptors
from cutback.server.plan_artifacts import public_config


def plan():
    execution, reference, candidate = artifacts()
    policies = decode_policy_bundle(
        {execution.reference.payload_id: reference, execution.candidates[0].payload_id: candidate},
        execution,
    )
    models = policy_descriptors(execution, policies)
    return ExperimentPlan(
        workflow=execution.source.workflow,
        dataset_hash="e" * 64,
        baseline=models["reference"],
        candidates=[models["routed-cache"]],
        metric={"kind": "exact_label", "minimum": 1, "min_test_samples": 2, "min_test_groups": 2},
        budget={"max_usd": "10", "max_requests": 100, "max_wall_seconds": 60},
        approved_providers=["openai"],
        split="validation",
        retain_case_outputs=True,
        content_nonce=uuid4(),
        evaluator_version="deterministic-v19",
        source_policy_execution=execution,
    )


def test_distinct_source_plan_and_safe_public_projection_without_payload_handles():
    value = plan()
    assert value.source_policy_execution is not None
    public = public_config(value.model_dump(mode="json"), redact_content_hashes=True)
    view = public["source_policy_execution"]
    assert "policy_sha256" not in str(view) and "payload_id" not in str(view)
    assert view["candidate_keys"] == ["routed-cache"] and view["phase"] == "evaluation"


@pytest.mark.parametrize(
    "field,value",
    [
        ("evaluator_version", "deterministic-v3"),
        ("retain_case_outputs", False),
        ("content_nonce", None),
        ("split", "train"),
        ("repeats", 2),
        ("approved_providers", ["anthropic"]),
    ],
)
def test_policy_refs_cannot_change_execution_or_approval_frame(field, value):
    raw = plan().model_dump(mode="json")
    raw[field] = value
    with pytest.raises(ValueError):
        ExperimentPlan.model_validate(raw)


def test_incomplete_server_adapter_cannot_fall_back_to_ordinary_native_execution():
    class NeverDispatch:
        def execute(self, *args, **kwargs):
            raise AssertionError("unfinished policy adapter dispatched a provider call")

    with pytest.raises(ValueError, match="SCOPED_EXECUTION_ADAPTER_REQUIRED"):
        run(plan(), {"items": []}, NeverDispatch(), approval_hash=None)


def dataset(value):
    return {
        "immutable_hash": value.dataset_hash,
        "items": [
            {"split": "validation", "case": {"input": text, "gold": "A"}}
            for text in ("owned short", "x" * 65)
        ],
    }


def test_forecast_reserves_native_and_both_cache_operations_with_owned_precision():
    value = plan()
    with localcontext() as context:
        context.prec = 6
        summary = plan_summary(value, dataset(value))
    assert summary["requests"] == 4
    assert (
        Decimal(summary["worst_case_modeled_usd"])
        == (upper_cost(value.baseline) + upper_cost(value.candidates[0]) + Decimal("0.000002")) * 2
    )
    assert summary["source_policy_execution"]["cache_operations_upper"] == 4
    assert summary["source_policy_execution"]["native_sdk_calls_upper"] == 4


def test_unknown_cache_cost_and_oversized_dataset_never_become_a_smaller_paid_trial():
    value = plan()
    execution = value.source_policy_execution
    candidate = execution.candidates[0].model_copy(update={"cache_operation_upper_usd": None})
    unknown = execution.model_copy(update={"candidates": [candidate]})
    with pytest.raises(ValueError, match="CACHE_OPERATION_COST_UNKNOWN"):
        plan_summary(value.model_copy(update={"source_policy_execution": unknown}), dataset(value))
    narrowed = execution.model_copy(update={"max_cases": 1})
    with pytest.raises(ValueError, match="CASE_BOUND_EXCEEDED_NO_SAMPLE_REDUCTION"):
        plan_summary(value.model_copy(update={"source_policy_execution": narrowed}), dataset(value))
    invalid = dataset(value)
    invalid["items"][0]["case"]["input"] = "é" * 65
    with pytest.raises(ValueError, match="CASE_OUTSIDE_APPROVED_SOURCE_BOUND"):
        plan_summary(value, invalid)
