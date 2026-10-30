"""Generated criteria cannot change owner thresholds, consume test rows or execute code."""

import json
from uuid import uuid4

import pytest

from cutback.evals.criteria_generation import (
    CriteriaGenerationPlan,
    CriteriaRequirements,
    decode,
    generation_input,
    generator_model,
    materialize,
)
from cutback.evals.experiments import Budget, ModelSpec


def requirements():
    return CriteriaRequirements(
        task_brief="Owned categorical support triage",
        minimum=0.95,
        max_regression=0,
        min_test_samples=50,
        min_test_groups=20,
        allowed_metrics=("exact_label", "json_schema"),
    )


def proposal():
    return {
        "criteria": [
            {
                "description": "Match the independently declared category",
                "metric_kind": "exact_label",
                "output_field": "category",
                "schema_definition": None,
                "training_anchors": ["training-1"],
            }
        ]
    }


def test_proposal_materialization_preserves_exact_owner_quality_thresholds_and_opaque_names():
    rules = requirements()
    parsed = decode(json.dumps(proposal()), rules, {"training-1"})
    criteria = materialize(parsed, rules)
    assert len(criteria) == 1 and criteria[0].required
    assert criteria[0].key == "criterion-1" and criteria[0].metric.minimum == 0.95
    assert criteria[0].metric.min_test_samples == 50 and criteria[0].metric.min_test_groups == 20
    assert criteria[0].metric.max_regression == 0
    assert "independently" not in criteria[0].model_dump_json()


@pytest.mark.parametrize(
    "changed",
    [
        {"minimum": 0.1},
        {"gold": "A"},
        {"source_code": "print('owned')"},
        {"training_anchors": ["test-1"]},
        {"metric_kind": "reference_match"},
    ],
)
def test_generated_authority_or_unseen_test_anchors_are_rejected(changed):
    document = proposal()
    document["criteria"][0].update(changed)
    with pytest.raises(ValueError):
        decode(json.dumps(document), requirements(), {"training-1"})


def test_duplicate_json_fields_and_duplicate_criteria_cannot_pad_the_scorecard():
    with pytest.raises(ValueError):
        decode('{"criteria":[],"criteria":[]}', requirements(), {"training-1"})
    document = proposal()
    document["criteria"].append(
        {**document["criteria"][0], "description": "A second name for the same check"}
    )
    with pytest.raises(ValueError, match="DUPLICATE_EVALUATOR"):
        decode(json.dumps(document), requirements(), {"training-1"})


@pytest.mark.parametrize(
    "schema",
    [{"$ref": "https://example.invalid/owned.json"}, {"type": "string", "pattern": "(a+)+$"}],
)
def test_generated_schema_has_the_same_no_remote_refs_or_regex_boundary(schema):
    document = proposal()
    document["criteria"][0].update(metric_kind="json_schema", schema_definition=schema)
    with pytest.raises(ValueError):
        decode(json.dumps(document), requirements(), {"training-1"})


def test_frozen_one_call_native_contract_does_not_allow_model_selected_request_options(monkeypatch):
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("PREPARATION_CANNOT_MAKE_PROVIDER_CALLS")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    model = generator_model(
        ModelSpec(
            key="owner-choice",
            provider="openai",
            model="gpt-4.1-mini",
            max_input_tokens=20000,
            max_output_tokens=2048,
        )
    )
    plan = CriteriaGenerationPlan(
        criteria_id=uuid4(),
        workflow="owned-triage",
        dataset_hash="a" * 64,
        input_payload_id=uuid4(),
        input_sha256="b" * 64,
        baseline=model,
        budget=Budget(max_usd="0.1", max_requests=1),
    )
    assert plan.candidates == [] and plan.operation == "criteria-generation-v1"
    for mutation in [
        {"budget": {"max_usd": "0.1", "max_requests": 2}},
        {
            "baseline": {
                **model.model_dump(mode="json"),
                "instruction": "a changed generator instruction",
            }
        },
        {"contract_sha256": "c" * 64},
    ]:
        with pytest.raises(ValueError):
            CriteriaGenerationPlan.model_validate({**plan.model_dump(mode="json"), **mutation})


def test_generation_input_contains_only_explicit_bounded_training_views():
    value = generation_input(
        requirements(), [{"anchor": "training-1", "input": "owned source", "gold": "A"}]
    )
    assert json.loads(value)["unseen_test_rows_supplied"] is False
    with pytest.raises(ValueError):
        generation_input(
            requirements(), [{"anchor": "test-1", "input": "owned source", "gold": "A"}]
        )
    with pytest.raises(ValueError):
        generation_input(
            requirements(), [{"anchor": "training-1", "input": "owned source", "gold": None}]
        )
