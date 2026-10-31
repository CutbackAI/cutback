"""A fixture-only source plan must never dispatch through a live native worker."""

from decimal import Decimal

import pytest
from support_agent import fresh_application
from support_evaluation import dataset_body, plan_for

from cutback.agent import analyze, load_session, selected_profile
from cutback.evals.datasets import prepare_dataset
from cutback.evals.experiments import Execution, ModelSpec, plan_summary, run


def source_fixture(tmp_path, **changes):
    fresh_application(tmp_path)
    analyze(tmp_path)
    profile = selected_profile(tmp_path, load_session(tmp_path))
    dataset = prepare_dataset(
        dataset_body(40).model_copy(update={"workflow": profile.binding.workflow_id})
    )
    baseline = ModelSpec.model_validate(profile.baseline())
    candidate = baseline.model_copy(update={"key": "cheaper", "model": "gpt-4.1-mini"})
    plan = plan_for(
        dataset,
        workflow=profile.binding.workflow_id,
        baseline=baseline.model_dump(mode="json"),
        candidates=[candidate.model_dump(mode="json")],
        source_binding=profile.binding.model_dump(mode="json"),
        approved_providers=["openai"],
        native_fixture={"version": "native-source-fixture-v1"},
        **changes,
    )
    return plan, dataset


class CountingExecutor:
    def __init__(self, *, synthetic=False):
        self.synthetic_transport = synthetic
        self.calls = 0

    def execute(self, model, text, timeout_seconds):
        self.calls += 1
        return Execution(
            "A",
            Decimal("0.00001" if model.key == "cheaper" else "0.0001"),
            1,
            input_tokens=24,
            output_tokens=2,
            response_model=model.model,
        )


@pytest.mark.parametrize("synthetic", [False, None, 1, "true"])
def test_fixture_only_source_refuses_non_fixture_executor_before_dispatch(tmp_path, synthetic):
    plan, dataset = source_fixture(tmp_path)
    executor = CountingExecutor(synthetic=synthetic)
    with pytest.raises(ValueError, match="SCRIPTED_NATIVE_REQUIRES_EXPLICIT_SYNTHETIC_EXECUTOR"):
        run(plan, dataset, executor, approval_hash=plan_summary(plan, dataset)["plan_hash"])
    assert executor.calls == 0


def test_fixture_native_results_never_become_verified_model_quality(tmp_path):
    plan, dataset = source_fixture(tmp_path)
    executor = CountingExecutor(synthetic=True)
    result = run(
        plan,
        dataset,
        executor,
        approval_hash=plan_summary(plan, dataset)["plan_hash"],
        independent_labels=True,
        representative=True,
    )
    assert result["status"] == "COMPLETED"
    assert result["native_fixture"]["real_model_quality_verified"] is False
    assert result["recommendations"]
    assert all(row["status"] != "VERIFIED_ON_HELDOUT_DATASET" for row in result["recommendations"])
    assert all(row["cost_evidence"] == "scripted_fixture_usage" for row in result["models"])


def test_unextended_plan_serialization_does_not_add_fixture_contract():
    dataset = prepare_dataset(dataset_body(10))
    assert "native_fixture" not in plan_for(dataset).model_dump(mode="json")


@pytest.mark.parametrize("value", [None, [], True, "SYNTHETIC_PRIVATE_READINESS_CANARY"])
def test_untyped_readiness_is_refused_without_echoing_the_response(value):
    from cutback.agent_preparation import require_protocol

    with pytest.raises(ValueError) as caught:
        require_protocol(lambda *args: value, native_fixture=True)
    assert "SYNTHETIC_PRIVATE_READINESS_CANARY" not in str(caught.value)
