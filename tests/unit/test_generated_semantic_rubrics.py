"""Generated rubric text never controls thresholds, native destinations or hidden examples."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from cutback.core.semantic_rubrics import SemanticRubricRequirements
from cutback.evals.criteria_generation import (
    CriteriaGenerationPlan,
    decode,
    generation_contract,
    generation_input,
    generator_model,
    parse_requirements,
)
from cutback.evals.experiments import ModelSpec
from cutback.evals.rubric_generation import (
    materialize_rubrics,
    reviewed_proxy_comparisons,
    rubric_judge,
)


def requirements():
    return SemanticRubricRequirements.model_validate(
        {
            "task_brief": "Owned synthetic support answer",
            "minimum": 0.9,
            "max_regression": 0,
            "dimensions": [
                {
                    "kind": "answer_correctness",
                    "requirement": "Match the permitted reference.",
                    "minimum": 1.0,
                },
                {
                    "kind": "retrieval_groundedness",
                    "requirement": "Use actually returned material.",
                    "minimum": 0.95,
                },
            ],
            "judge": {
                "model": "gpt-4.1-mini",
                "max_input_tokens": 24000,
                "max_output_tokens": 1024,
            },
        }
    )


def proposed():
    return {
        "criteria": [
            {
                "dimension": "dimension-1",
                "judge_rubric": "Compare the answer with reference evidence.",
                "training_anchors": ["training-1"],
            },
            {
                "dimension": "dimension-2",
                "judge_rubric": "Use quotations from returned documents.",
                "training_anchors": ["training-1"],
            },
        ]
    }


def test_owner_limits_and_opaque_training_views_bind_a_distinct_generation_contract():
    owner = requirements()
    assert parse_requirements(owner.model_dump(mode="json")) == owner
    frame = json.loads(
        generation_input(
            owner,
            [{"anchor": "training-1", "input": "Owned training input", "gold": "Owned reference"}],
        )
    )
    assert frame["unseen_test_rows_supplied"] is False
    assert [d["id"] for d in frame["dimensions"]] == ["dimension-1", "dimension-2"]
    proposal = decode(json.dumps(proposed()), owner, {"training-1"})
    spec = materialize_rubrics(proposal, owner)
    assert [c.minimum for c in spec.criteria] == [1.0, 0.95]
    assert [c.kind for c in spec.criteria] == [d.kind for d in owner.dimensions]
    assert owner.dimensions[0].requirement in spec.criteria[0].judge_rubric
    model = generator_model(
        ModelSpec(key="generator", provider="openai", model="gpt-4.1-mini"), semantic=True
    )
    plan = CriteriaGenerationPlan(
        criteria_id=uuid4(),
        workflow="owned-task",
        dataset_hash="a" * 64,
        input_payload_id=uuid4(),
        input_sha256="b" * 64,
        baseline=model,
        budget={"max_usd": "1", "max_requests": 1},
        **generation_contract(owner),
    )
    assert plan.operation == "criteria-generation-v2"
    with pytest.raises(ValueError):
        CriteriaGenerationPlan.model_validate(
            {**plan.model_dump(mode="json"), "operation": "criteria-generation-v1"}
        )
    judge = rubric_judge(owner)
    assert judge.provider == "openai" and judge.request_contract.sdk_surface == "responses.create"
    assert "http" not in json.dumps(judge.request_contract.options)


@pytest.mark.parametrize(
    "change", ["threshold", "missing", "duplicate", "unseen", "code", "duplicate_json"]
)
def test_model_cannot_change_owner_scope_or_add_unseen_ground_truth(change):
    value = proposed()
    if change == "threshold":
        value["criteria"][0]["minimum"] = 0
    elif change == "missing":
        value["criteria"].pop()
    elif change == "duplicate":
        value["criteria"][1] = value["criteria"][0]
    elif change == "unseen":
        value["criteria"][0]["training_anchors"] = ["test-1"]
    elif change == "code":
        value["criteria"][0]["evaluator_code"] = "must not execute"
    raw = json.dumps(value)
    if change == "duplicate_json":
        raw = '{"criteria":[],"criteria":' + json.dumps(value["criteria"]) + "}"
    with pytest.raises(ValueError):
        decode(raw, requirements(), {"training-1"})


def test_declared_proxy_pass_rate_allows_owner_tolerance_without_inventing_confidence():
    plan = SimpleNamespace(
        evaluator_version="deterministic-v14",
        metric=SimpleNamespace(
            minimum=0.8, max_regression=0.1, min_test_samples=10, min_test_groups=3
        ),
    )
    baseline = {
        "model": {"key": "baseline"},
        "metric_min": 0.9,
        "metric_max": 0.9,
        "quality": {"sample_n": 10, "independent_group_n": 3, "unknown": 0, "errors": 0},
        "cost_complete": True,
        "known_cost_subtotal_usd": "0.50",
    }
    candidate = {
        **baseline,
        "model": {"key": "candidate"},
        "metric_min": 0.85,
        "metric_max": 0.85,
        "known_cost_subtotal_usd": "0.20",
    }
    result = reviewed_proxy_comparisons(plan, [baseline, candidate])[0]
    assert (
        result["quality_gate_passed"] is True
        and result["sample_modeled_cost_difference_usd"] == "0.30"
    )
    assert result["statistical_gate_passed"] is False and result["source_patch_eligible"] is False
    unknown = reviewed_proxy_comparisons(plan, [baseline, {**candidate, "metric_min": None}])[0]
    assert unknown["quality_gate_passed"] is False and unknown["cheaper_on_sample"] is True
    incomplete = reviewed_proxy_comparisons(
        plan, [baseline, {**candidate, "cost_complete": False}]
    )[0]
    assert (
        incomplete["quality_gate_passed"] is False
        and incomplete["sample_modeled_cost_difference_usd"] is None
    )


def test_unicode_rubrics_fit_the_judge_and_oversized_escaped_text_is_rejected():
    raw = requirements().model_dump(mode="json")
    raw["dimensions"] = raw["dimensions"][:1]
    raw["dimensions"][0]["requirement"] = "я" * 800
    owner = SemanticRubricRequirements.model_validate(raw)
    value = proposed()
    value["criteria"] = value["criteria"][:1]
    value["criteria"][0]["judge_rubric"] = "я" * 1500
    proposal = decode(json.dumps(value), owner, {"training-1"})
    assert "я" * 1500 in materialize_rubrics(proposal, owner).criteria[0].judge_rubric
    owner = owner.model_copy(
        update={"dimensions": [owner.dimensions[0].model_copy(update={"requirement": '"' * 2000})]}
    )
    value["criteria"][0]["judge_rubric"] = '"' * 4000
    with pytest.raises(ValueError):
        decode(json.dumps(value), owner, {"training-1"})
