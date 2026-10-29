import copy
import json

import pytest
from support_evaluation import SyntheticExecutor, dataset_body, plan_for

from cutback.core.request_profiles import RequestContract
from cutback.evals.datasets import prepare_dataset
from cutback.evals.experiments import plan_summary, run
from cutback.evals.input_groups import effective_input_groups


def fixture(*, cross_split=False):
    data = dataset_body(200)
    for case in data.cases:
        case.input = json.dumps({"question": "owned fixed task", "unused": case.input})
    dataset = prepare_dataset(data)
    if not cross_split:
        # Explicit test-only frozen corpus with every family in the test split.
        for item in dataset["items"]:
            item["split"] = "test"
    contract = {
        "sdk_surface": "responses.create",
        "parameters": ["record"],
        "payload": {
            "op": "lookup",
            "value": {"op": "parameter", "name": "record"},
            "key": "question",
        },
    }
    plan = plan_for(
        dataset,
        baseline={
            "key": "baseline",
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "request_contract": contract,
        },
        candidates=[
            {
                "key": "candidate",
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "request_contract": contract,
            }
        ],
        approved_providers=["openai"],
        budget={"max_usd": "10", "max_requests": 500},
    )
    return plan, dataset


def test_unused_record_fields_cannot_create_independent_native_examples():
    plan, dataset = fixture()
    before = copy.deepcopy(dataset)
    result = run(
        plan,
        dataset,
        SyntheticExecutor(),
        approval_hash=plan_summary(plan, dataset)["plan_hash"],
        independent_labels=True,
    )
    assert result["status"] == "COMPLETED"
    assert result["models"][0]["quality"]["independent_group_n"] == 1
    assert not result["recommendations"][0]["quality_gate_passed"]
    assert result["input_grouping"]["raw_groups"] == 200
    assert result["input_grouping"]["selected_effective_groups"] == 1
    assert result["input_grouping"]["cross_dataset_rendered_input_history"] == "unknown"
    assert "owned fixed task" not in json.dumps(result)
    assert dataset == before


def test_native_collision_across_splits_fails_before_any_call_or_reservation():
    plan, dataset = fixture(cross_split=True)
    executor = SyntheticExecutor()
    reservations = []
    with pytest.raises(ValueError, match="NATIVE_INPUT_FAMILY_SPANS_DATASET_SPLITS"):
        run(
            plan,
            dataset,
            executor,
            approval_hash=plan_summary(plan, dataset)["plan_hash"],
            reserve_call=lambda *args, **kwargs: reservations.append(args),
            record_call=lambda *args, **kwargs: None,
        )
    assert executor.calls == reservations == []


def test_all_frozen_cases_checked_before_sample_limit():
    plan, dataset = fixture(cross_split=True)
    plan.sample_limit = 1
    with pytest.raises(ValueError, match="NATIVE_INPUT_FAMILY_SPANS_DATASET_SPLITS"):
        effective_input_groups(plan, dataset)


def test_duplicate_in_one_candidate_merges_for_the_entire_paired_comparison():
    plan, dataset = fixture()
    plan.baseline.request_contract = None
    _, receipt = effective_input_groups(plan, dataset)
    assert receipt["effective_groups"] == 1


def test_structural_payload_boundaries_are_preserved():
    plan, dataset = fixture()
    for index, item in enumerate(dataset["items"]):
        item["case"]["input"] = json.dumps(
            {
                "question": [
                    {"role": "user", "content": "alpha"},
                    {"role": "user", "content": "beta"},
                ]
                if index % 2
                else [{"role": "user", "content": "alpha beta"}]
            }
        )
    _, receipt = effective_input_groups(plan, dataset)
    assert receipt["effective_groups"] == 2


def test_grouping_work_is_bounded_and_diagnostics_never_echo_inputs(monkeypatch):
    import cutback.evals.input_groups as module

    plan, dataset = fixture()
    monkeypatch.setattr(module, "MAX_PROJECTED_BYTES", 1)
    with pytest.raises(ValueError, match="^NATIVE_INPUT_GROUPING_WORK_LIMIT$"):
        effective_input_groups(plan, dataset)
    monkeypatch.setattr(module, "MAX_PROJECTED_BYTES", 32 * 1048576)
    dataset["items"][0]["case"]["input"] = "PRIVATE SYNTHETIC INVALID JSON"
    with pytest.raises(ValueError, match="^NATIVE_INPUT_GROUPING_RENDER_FAILED$"):
        effective_input_groups(plan, dataset)


def test_untransformed_cases_preserve_existing_families():
    plan, dataset = fixture()
    plan.baseline.request_contract = plan.candidates[0].request_contract = None
    view, receipt = effective_input_groups(plan, dataset)
    assert view is dataset
    assert receipt is None


def test_equal_gold_does_not_merge_distinct_native_requests():
    plan, dataset = fixture()
    # Existing numeric normalization deliberately treats changing only numbers as
    # a near duplicate; use alphabetic synthetic identifiers for distinct tasks.
    for index, item in enumerate(dataset["items"]):
        item["case"]["input"] = json.dumps(
            {"question": "task " + chr(65 + index // 26) + chr(65 + index % 26)}
        )
    view, receipt = effective_input_groups(plan, dataset)
    assert receipt["effective_groups"] == 200
    assert [item["group_hash"] for item in view["items"]] == [
        item["group_hash"] for item in dataset["items"]
    ]


def test_customer_family_and_native_collisions_merge_transitively():
    plan, dataset = fixture()
    dataset["items"] = dataset["items"][:3]
    first, second, third = dataset["items"]
    second["group_hash"] = first["group_hash"]
    first["case"]["input"] = json.dumps({"question": "alpha"})
    second["case"]["input"] = third["case"]["input"] = json.dumps({"question": "beta"})
    _, receipt = effective_input_groups(plan, dataset)
    assert receipt["raw_groups"] == 2
    assert receipt["effective_groups"] == 1


def test_multi_parameter_prompt_ignores_unused_variables_and_preserves_order():
    plan, dataset = fixture()
    contract = RequestContract(
        sdk_surface="responses.create",
        version="native-prompt-v1",
        parameters=["question", "unused"],
        payload={
            "op": "dict",
            "items": {
                "instructions": {"op": "literal", "value": "Return a label"},
                "input": {"op": "parameter", "name": "question"},
            },
        },
    )
    plan.baseline.request_contract = plan.candidates[0].request_contract = contract
    _, receipt = effective_input_groups(plan, dataset)
    assert receipt["effective_groups"] == 1
    assert list(contract.render(dataset["items"][0]["case"]["input"])) == ["instructions", "input"]


def test_router_uses_its_actual_target_projection():
    from cutback.evals.experiments import ModelSpec, RouteSpec

    plan, dataset = fixture()
    target = plan.baseline.model_copy(deep=True)
    plan.baseline = ModelSpec(
        key="baseline",
        provider="router",
        model="byte-length",
        routes=[RouteSpec(max_input_bytes=1000, target=target)],
    )
    plan.candidates[0].request_contract = None
    _, receipt = effective_input_groups(plan, dataset)
    assert receipt["effective_groups"] == 1


def test_authority_is_rechecked_during_bounded_preparation():
    plan, dataset = fixture()
    checks = []
    effective_input_groups(plan, dataset, check=lambda: checks.append(True))
    assert len(checks) == 4


@pytest.mark.parametrize("case_limit", [1, 200])
def test_input_preparation_cannot_reset_the_approved_wall_clock(monkeypatch, case_limit):
    from cutback.core.request_profiles import RequestContract
    from cutback.evals import experiments

    plan, dataset = fixture()
    dataset["items"] = dataset["items"][:case_limit]
    plan.budget.max_wall_seconds = 1
    clock = [0.0]
    original = RequestContract.render

    def render(self, text):
        if clock[0] == 0:
            clock[0] = 2
        return original(self, text)

    monkeypatch.setattr(experiments.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(RequestContract, "render", render)
    executor = SyntheticExecutor()
    reservations = []
    with pytest.raises(ValueError, match="NATIVE_INPUT_GROUPING_WALL_LIMIT"):
        run(
            plan,
            dataset,
            executor,
            approval_hash=plan_summary(plan, dataset)["plan_hash"],
            reserve=lambda *args: reservations.append(args),
        )
    assert executor.calls == reservations == []
