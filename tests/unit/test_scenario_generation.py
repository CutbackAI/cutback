"""Generated user text cannot weaken the original tool/task contract or become held-out gold."""

import json
from uuid import uuid4

import pytest
from test_scenarios import owned_source

from cutback.core.agent_turns import AgentTurnFrame
from cutback.core.scenario_proposals import ScenarioRequirements
from cutback.evals.datasets import Case, DatasetImport, prepare_dataset
from cutback.evals.experiments import Budget, ModelSpec
from cutback.evals.scenario_generation import (
    ScenarioGenerationPlan,
    decode,
    generation_input,
    generator_model,
    materialize,
)


def requirements(**changes):
    return ScenarioRequirements.model_validate(
        {
            "task_brief": (
                "Exercise the original support task without changing its expected outcome."
            ),
            "coverage_goals": [
                {
                    "key": "clarification",
                    "description": "Ask for clarification of the original task.",
                },
                {"key": "concise", "description": "Request a concise answer."},
            ],
            **changes,
        }
    )


def proposed():
    return {
        "personas": [
            {
                "key": "concise-user",
                "role": "customer",
                "communication_style": "concise",
                "situation": "Needs the original answer explained briefly.",
            }
        ],
        "scenarios": [
            {
                "source_anchor": "training-1",
                "persona_key": "concise-user",
                "coverage_goal_keys": ["clarification"],
                "additional_context": "Please explain the original answer briefly.",
                "user_followups": ["Please repeat the answer to my original question."],
            }
        ],
    }


def sources():
    return {"training-1": owned_source().cases[0]}


def test_materialized_cases_keep_every_original_tool_assertion_limit_and_source_family():
    source = sources()
    original = source["training-1"].model_dump(mode="json")
    req = requirements()
    proposal = decode(json.dumps(proposed()), req, source)
    result = materialize(proposal, req, source)
    assert source["training-1"].model_dump(mode="json") == original
    anchor, changed = [Case.model_validate(value) for value in result["cases"]]
    assert changed.input.startswith(original["input"])
    assert changed.group == anchor.group
    assert changed.gold == anchor.gold and changed.reference == anchor.reference
    before = anchor.episode.model_dump(mode="json")
    after = changed.episode.model_dump(mode="json")
    assert after.pop("user_followups")[: len(before["user_followups"])] == before.pop(
        "user_followups"
    )
    assert after == before
    assert result["coverage"] == [
        {"goal_key": "clarification", "proposed_scenarios": 1},
        {"goal_key": "concise", "proposed_scenarios": 0},
    ]
    assert result["coverage_scope"] == "proposer_declared_not_measured"
    assert result["review_required"] and not result["independent_labels"]
    assert not result["representative"] and not result["reactive_user_simulation"]
    body = DatasetImport(
        policy_id=str(uuid4()),
        workflow="owned-scenarios",
        source_type="synthetic_fixture",
        provenance_ref="owned-generated-fixture",
        cases=[anchor, changed],
    )
    dataset = prepare_dataset(body)
    assert dataset["group_count"] == 1
    assert len({item["split"] for item in dataset["items"]}) == 1


def test_generator_view_has_opaque_training_anchors_and_omits_gold_tools_and_reference():
    source = sources()
    source["training-1"] = source["training-1"].model_copy(
        update={
            "gold": "owned-private-gold",
            "reference": "owned-private-reference",
        }
    )
    value = generation_input(requirements(), source)
    assert "owned-private-gold" not in value and "owned-private-reference" not in value
    parsed = json.loads(value)
    assert parsed["unseen_test_rows_supplied"] is False
    assert parsed["gold_and_tool_results_supplied"] is False
    assert set(parsed["training"][0]) == {
        "anchor",
        "input",
        "existing_user_followups",
        "additional_user_turns_allowed",
    }
    assert parsed["training"][0]["anchor"] == "training-1"
    with pytest.raises(ValueError, match="OPAQUE_TRAINING_ANCHORS"):
        generation_input(requirements(), {"real-case-name": source["training-1"]})


@pytest.mark.parametrize(
    "damage", ["anchor", "persona", "goal", "turns", "gold", "tools", "duplicate", "empty"]
)
def test_generated_scope_tampering_and_fabricated_authority_are_refused(damage):
    value = proposed()
    variant = value["scenarios"][0]
    if damage == "anchor":
        variant["source_anchor"] = "training-2"
    elif damage == "persona":
        variant["persona_key"] = "unknown-persona"
    elif damage == "goal":
        variant["coverage_goal_keys"] = ["not-owner-goal"]
    elif damage == "turns":
        variant["user_followups"] *= 3
    elif damage == "gold":
        variant["gold"] = "invented answer"
    elif damage == "tools":
        variant["tools"] = [{"key": "invented_tool"}]
    elif damage == "duplicate":
        value["scenarios"] *= 2
    else:
        variant["additional_context"], variant["user_followups"] = "", []
    with pytest.raises(ValueError):
        decode(json.dumps(value), requirements(), sources())


def test_generated_user_turns_cannot_widen_original_conversation_bound():
    source = sources()
    scenario = source["training-1"].episode
    source["training-1"].episode = scenario.model_copy(
        update={
            "max_turns": len(scenario.user_followups) + 1,
        }
    )
    req = requirements()
    proposal = decode(json.dumps(proposed()), req, source)
    with pytest.raises(ValueError, match="ORIGINAL_CONVERSATION_BOUND"):
        materialize(proposal, req, source)


def test_plain_text_scenarios_preserve_labels_without_inventing_multi_turn_execution():
    source = {"training-1": Case(id="owned-text", input="Priority: normal.", gold="Normal")}
    req = requirements()
    proposal = decode(json.dumps(proposed()), req, source)
    with pytest.raises(ValueError, match="CANNOT_ADD_MULTI_TURN"):
        materialize(proposal, req, source)
    value = proposed()
    value["scenarios"][0]["user_followups"] = []
    proposal = decode(json.dumps(value), req, source)
    result = materialize(proposal, req, source)
    assert all(case["gold"] == "Normal" and not case.get("episode") for case in result["cases"])
    assert (
        json.loads(generation_input(req, source))["training"][0]["additional_user_turns_allowed"]
        is False
    )


def test_json_and_byte_refusals_never_echo_private_generated_text():
    marker = "owned-private-generated-content"
    for text in ["{broken" + marker, '{"personas":[],"personas":[]}', marker * 2000]:
        with pytest.raises(ValueError) as result:
            decode(text, requirements(), sources())
        assert marker not in str(result.value)


def test_generation_plan_freezes_exact_one_call_native_frame_and_known_price():
    raw = ModelSpec(
        key="owned-proposer", provider="openai", model="gpt-4.1-mini", max_output_tokens=2048
    )
    model = generator_model(raw)
    plan = ScenarioGenerationPlan(
        scenario_id=uuid4(),
        workflow="owned-scenarios",
        dataset_hash="a" * 64,
        input_payload_id=uuid4(),
        input_sha256="b" * 64,
        baseline=model,
        budget=Budget(max_usd="1", max_requests=1),
    )
    assert plan.operation == "scenario-generation-v1" and plan.candidates == []
    assert model.request_contract.render("owned input") == "owned input"
    for change in [
        {"budget": {"max_usd": "1", "max_requests": 2}},
        {"contract_sha256": "f" * 64},
        {"price_sha256": "f" * 64},
        {"scenario_id": str(uuid4()).replace("-", "")[:-1]},
    ]:
        with pytest.raises(ValueError):
            ScenarioGenerationPlan.model_validate({**plan.model_dump(mode="json"), **change})
    with pytest.raises(ValueError, match="AGENT_TURN_CONTENT"):
        generator_model(
            raw.with_agent_turn(
                AgentTurnFrame(
                    surface="responses.create",
                    messages=[{"role": "user", "text": "Owned request"}],
                )
            )
        )
