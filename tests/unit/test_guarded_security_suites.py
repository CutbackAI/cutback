"""A guarded suite needs its own approval and cannot hide missing check coverage."""

from copy import deepcopy

import pytest
from unit.test_guarded_tool_contract import guarded_plan
from unit.test_security_suites import row, suite

from cutback.core.security_suites import SecurityEvaluationSpec
from cutback.evals.experiments import ExperimentPlan
from cutback.evals.security_suites import summarize_security


def test_combined_security_and_runtime_checks_require_explicit_v11_approval():
    _, original = guarded_plan()
    raw = original.model_dump(mode="json")
    security = SecurityEvaluationSpec(suite=suite().security_suite).model_dump(mode="json")
    raw["episode_execution"]["security_suite"] = security
    with pytest.raises(ValueError):
        ExperimentPlan.model_validate(raw)
    raw["episode_execution"]["version"] = "native-isolated-record-guarded-security-v1"
    with pytest.raises(ValueError):
        ExperimentPlan.model_validate(raw)
    raw["evaluator_version"] = "deterministic-v11"
    approved = ExperimentPlan.model_validate(raw)
    assert (
        approved.episode_execution.security_suite.suite
        == SecurityEvaluationSpec.model_validate(security).suite
    )
    assert (
        approved.episode_execution.isolated_tools.runtime_guardrails
        == original.episode_execution.isolated_tools.runtime_guardrails
    )
    assert (
        ExperimentPlan.model_validate(original.model_dump()).model_dump_json()
        == original.model_dump_json()
    )
    for change in ("unguarded_execution", "missing_suite", "missing_binding"):
        invalid = deepcopy(raw)
        spec = invalid["episode_execution"]
        if change == "unguarded_execution":
            spec["version"] = "native-isolated-record-security-v1"
        elif change == "missing_suite":
            del spec["security_suite"]
        else:
            del spec["isolated_tools"]["runtime_guardrails"]
        with pytest.raises(ValueError):
            ExperimentPlan.model_validate(invalid)


def test_guard_coverage_is_separate_from_security_and_useful_work_and_fpr_stays_unknown():
    spec = SecurityEvaluationSpec(
        suite=suite().security_suite,
        minimum_families=1,
        required_categories=["instruction_override"],
    )
    rows = [
        {
            **row(str(i), arm),
            "guardrail_checks": 4,
            "guardrail_coverage_complete": True,
            "guardrail_policy_decisions_known": True,
            "guardrail_policy_stopped": False,
        }
        for i in range(100)
        for arm in ("benign", "adversarial")
    ]
    before = summarize_security(spec, rows, require_guard_coverage=True)
    assert before["gate"] == "criteria_satisfied_for_declared_suite_only"
    assert before["runtime_guardrails"]["coverage_complete"]
    assert before["runtime_guardrails"]["checks"] == 800
    assert before["runtime_guardrails"]["benign_family_interruption"]["value"] == 0
    assert before["guardrail_false_positive_rate"] is None
    rows[0].update(guardrail_coverage_complete=False, guardrail_policy_decisions_known=False)
    missing = summarize_security(spec, rows, require_guard_coverage=True)
    assert missing["gate"] == "insufficient_evidence"
    assert missing["runtime_guardrails"]["incomplete_or_unknown_case_executions"] == 1
    assert missing["runtime_guardrails"]["benign_family_interruption"]["value"] is None
    assert missing["benign_utility"]["value"] == 1
    # A repeat is not a new independent family; a known interruption survives
    # another repeat whose detector coverage is unavailable.
    rows.append({**rows[0], "guardrail_policy_stopped": True, "repeat": 1})
    mixed = summarize_security(spec, rows, require_guard_coverage=True)
    assert mixed["family_n"] == 100 and mixed["case_execution_n"] == 201
    assert mixed["runtime_guardrails"]["benign_family_interruption"]["positive_families"] == 1
    unknown = summarize_security(spec, [row("missing", "benign")], require_guard_coverage=True)
    assert unknown["runtime_guardrails"]["coverage_complete"] is None
    assert unknown["runtime_guardrails"]["policy_decisions_known"] is None
    assert unknown["runtime_guardrails"]["checks"] is None
    assert unknown["runtime_guardrails"]["known_checks_subtotal"] == 0
