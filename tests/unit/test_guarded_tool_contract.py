from copy import deepcopy
from uuid import uuid4

import pytest
from unit.test_guardrails import policy
from unit.test_isolated_episode_contract import isolated_plan

from cutback.core.guardrails import GuardrailBinding
from cutback.evals.experiments import ExperimentPlan


def guarded_plan():
    project, principal, broker = uuid4(), uuid4(), uuid4()
    original = isolated_plan(project, principal, broker, "sha256:" + "a" * 64)
    definition = policy(
        project_id=str(project),
        principal_id=str(principal),
        workflows=["classify"],
        protected_stages=["tool_input", "tool_output"],
        rules=[
            {
                "kind": "literal_deny",
                "key": "owned-tool-boundary",
                "stages": ["tool_input", "tool_output"],
                "terms": ["SYNTHETIC_GUARDED_TOOL_LITERAL"],
            }
        ],
    )
    binding = GuardrailBinding(service_id=uuid4(), **definition.public_metadata())
    raw = original.model_dump(mode="json")
    raw["evaluator_version"] = "deterministic-v10"
    raw["episode_execution"]["version"] = "native-isolated-record-guarded-v1"
    raw["episode_execution"]["isolated_tools"].update(
        {
            "version": "isolated-record-guarded-tools-v1",
            "runtime_guardrails": binding.model_dump(mode="json"),
        }
    )
    return original, ExperimentPlan.model_validate(raw)


def test_guards_require_a_separate_frozen_plan_without_changing_released_serialization():
    original, guarded = guarded_plan()
    assert original.evaluator_version == "deterministic-v8"
    assert "runtime_guardrails" not in original.model_dump_json()
    assert guarded.evaluator_version == "deterministic-v10"
    assert guarded.episode_execution.isolated_tools.runtime_guardrails.environment == "staging"
    assert "SYNTHETIC_GUARDED_TOOL_LITERAL" not in guarded.model_dump_json()
    for change in (
        "evaluator",
        "execution",
        "missing_binding",
        "wrong_owner",
        "wrong_workflow",
        "large_bounds",
        "wrong_stages",
    ):
        raw = deepcopy(guarded.model_dump(mode="json"))
        target = raw["episode_execution"]["isolated_tools"]
        if change == "evaluator":
            raw["evaluator_version"] = "deterministic-v8"
        if change == "execution":
            raw["episode_execution"]["version"] = "native-isolated-record-episode-v1"
        if change == "missing_binding":
            del target["runtime_guardrails"]
        if change == "wrong_owner":
            target["runtime_guardrails"]["principal_id"] = str(uuid4())
        if change == "wrong_workflow":
            raw["workflow"] = "other"
        if change == "large_bounds":
            target["max_sessions"] = 129
        if change == "wrong_stages":
            target["runtime_guardrails"]["protected_stages"] = ["input", "output"]
            target["runtime_guardrails"]["stage_rule_counts"] = {"input": 1, "output": 1}
        with pytest.raises(ValueError):
            ExperimentPlan.model_validate(raw)
