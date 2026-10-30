import json
from copy import deepcopy
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from cutback.cli import app
from cutback.core.security_suites import SecurityEvaluationSpec, manifest_hash
from cutback.evals.datasets import DatasetImport, prepare_dataset
from cutback.evals.security_suites import (
    compare_security,
    summarize_security,
    validate_security_selection,
)
from cutback.sample_app.security_suite import draft_security_suite


def suite(families=20):
    return draft_security_suite(uuid4(), uuid4(), uuid4(), families=families)


def resign(raw):
    from cutback.core.security_suites import SecuritySuiteIdentity

    identity = SecuritySuiteIdentity.model_validate(raw["security_suite"])
    raw["security_suite"]["manifest_sha256"] = manifest_hash(identity, raw["cases"])
    return raw


def row(family, arm, *, attack=False, utility=True, repeat=0):
    return {
        "family_token": family,
        "arm": arm,
        "category": "none" if arm == "benign" else "instruction_override",
        "attack_success": attack,
        "business_utility": utility,
        "repeat": repeat,
        "legitimate_tool_calls": 2,
        "legitimate_tool_denials": 0,
        "tool_legitimacy_unknown": 0,
    }


def test_manifest_changes_require_review_and_pair_authority_cannot_drift():
    body = suite()
    raw = body.model_dump(mode="json")
    raw["cases"][1]["input"] += " NEW ATTACK"
    with pytest.raises(ValueError, match="MANIFEST_CONTENT_CHANGED"):
        DatasetImport.model_validate(raw)
    with pytest.raises(ValueError, match="UNDECLARED_USER_INPUT_MUTATION"):
        DatasetImport.model_validate(resign(raw))
    raw = body.model_dump(mode="json")
    raw["cases"][1]["episode"]["grants"][0]["read_fields"].append("private_note")
    with pytest.raises(ValueError, match="TRUSTED_AUTHORITY"):
        DatasetImport.model_validate(resign(raw))
    raw = body.model_dump(mode="json")
    raw["cases"][1]["episode"]["seeds"][0]["fields"]["status"] = "resolved"
    with pytest.raises(ValueError, match="TASK_STATE"):
        DatasetImport.model_validate(resign(raw))
    raw = body.model_dump(mode="json")
    raw["cases"] = raw["cases"][1:]
    with pytest.raises(ValueError, match="ONE_BENIGN"):
        DatasetImport.model_validate(resign(raw))


def test_pairs_share_holdout_even_when_caller_changes_group_ids():
    body = suite(30)
    raw = body.model_dump(mode="json")
    for index, case in enumerate(raw["cases"]):
        case["group"] = "different-" + str(index)
    prepared = prepare_dataset(DatasetImport.model_validate(resign(raw)))
    splits = {}
    for item in prepared["items"]:
        key = item["case"]["security"]["family"]
        previous = splits.setdefault(key, (item["split"], item["group_hash"]))
        assert previous == (item["split"], item["group_hash"])
    assert len(splits) == prepared["group_count"] == 30
    assert set(prepared["split_counts"]) == {"train", "validation", "test"}
    assert "security" not in json.dumps(prepared["task_inference"])


def test_security_selection_refuses_partial_families_and_wrong_manifest():
    from types import SimpleNamespace

    body = suite()
    prepared = prepare_dataset(body)
    prepared["security_suite"] = body.security_suite.model_dump(mode="json")
    spec = SecurityEvaluationSpec(suite=body.security_suite)
    plan = SimpleNamespace(
        episode_execution=SimpleNamespace(security_suite=spec), split="test", sample_limit=None
    )
    validate_security_selection(plan, prepared)
    plan.sample_limit = 1
    with pytest.raises(ValueError, match="WHOLE_PAIRED"):
        validate_security_selection(plan, prepared)
    plan.sample_limit = None
    prepared["security_suite"] = {**prepared["security_suite"], "revision": "other"}
    with pytest.raises(ValueError, match="IDENTITY_MISMATCH"):
        validate_security_selection(plan, prepared)


def test_repeats_do_not_inflate_independent_n_and_unknowns_never_become_safe():
    spec = SecurityEvaluationSpec(suite=suite().security_suite)
    rows = [
        row("one-family", arm, repeat=repeat)
        for repeat in range(5)
        for arm in ("benign", "adversarial")
    ]
    report = summarize_security(spec, rows)
    assert report["family_n"] == 1 and report["case_execution_n"] == 10
    assert report["gate"] == "insufficient_evidence"
    assert report["attack_success"]["confidence_95"]["high"] > 0.7
    assert report["guardrail_false_positive_rate"] is None
    rows.append(row("another", "adversarial", attack=None, utility=None))
    report = summarize_security(spec, rows)
    assert report["attack_success"]["value"] is None
    assert report["attack_success"]["known_subset_value"] == 0
    assert report["attack_success"]["confidence_95"]["high"] is None
    assert report["benign_utility"]["unknown_families"] == 1


def test_breach_is_retained_even_if_another_repeat_is_unknown_and_refusal_is_useless():
    spec = SecurityEvaluationSpec(suite=suite().security_suite)
    rows = [
        row("one", "benign", utility=False),
        row("one", "adversarial", attack=True),
        row("one", "adversarial", attack=None, utility=None),
    ]
    report = summarize_security(spec, rows)
    assert report["attack_success"]["value"] == 1
    assert report["benign_utility"]["value"] == 0
    assert report["attacked_utility"]["value"] is None
    assert report["outcome_matrix"] == {"safe_useless": 1, "compromised_useful": 1, "unknown": 1}


def test_candidate_cannot_hide_lost_benign_utility_behind_lower_attack_rate():
    spec = SecurityEvaluationSpec(
        suite=suite().security_suite,
        minimum_families=1,
        required_categories=["instruction_override"],
    )
    baseline = [row(str(i), arm) for i in range(100) for arm in ("benign", "adversarial")]
    candidate = deepcopy(baseline)
    for value in candidate:
        if int(value["family_token"]) < 25 and value["arm"] == "benign":
            value["business_utility"] = False
    before, after = summarize_security(spec, baseline), summarize_security(spec, candidate)
    comparison = compare_security(spec, before, after, baseline, candidate)
    assert before["gate"] == "criteria_satisfied_for_declared_suite_only"
    assert comparison["gate"] == "criteria_not_satisfied"
    assert comparison["baseline_comparison"]["lost_utility_families"] == 25
    assert comparison["baseline_comparison"]["regression_fraction"] == 0.25


def test_cli_is_offline_and_does_not_print_attack_or_canary_content(tmp_path):
    output = tmp_path / "suite.json"
    result = CliRunner().invoke(
        app,
        [
            "security",
            "generate",
            "--project",
            str(uuid4()),
            "--principal",
            str(uuid4()),
            "--policy",
            str(uuid4()),
            "--families",
            "7",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "CANARY" not in result.output and "OVERRIDDEN" not in result.output
    sealed = tmp_path / "sealed.json"
    result = CliRunner().invoke(
        app, ["security", "seal", str(output), "--revision", "reviewed-2", "--output", str(sealed)]
    )
    assert result.exit_code != 0 and not sealed.exists()
    result = CliRunner().invoke(
        app,
        [
            "security",
            "seal",
            str(output),
            "--revision",
            "reviewed-2",
            "--reviewed",
            "--output",
            str(sealed),
        ],
    )
    assert result.exit_code == 0, result.output
    before, after = json.loads(output.read_text()), json.loads(sealed.read_text())
    assert after["security_suite"]["revision"] == "reviewed-2"
    assert "content_nonce" not in after["security_suite"]
    assert (
        after["cases"][0]["security"]["content_nonce"]
        != before["cases"][0]["security"]["content_nonce"]
    )
    for first, second in zip(before["cases"], after["cases"], strict=True):
        first["security"].pop("content_nonce")
        second["security"].pop("content_nonce")
        assert first == second
    result = CliRunner().invoke(app, ["security", "validate", str(output)])
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary["paid_calls"] == 0 and summary["case_n"] == 14
    assert "content_nonce" not in result.output
    assert "CANARY" not in result.output and "OVERRIDDEN" not in result.output


def test_larger_suite_bounds_do_not_widen_released_v8_contracts():
    from unit.test_isolated_episode_contract import isolated_plan

    from cutback.core.isolated_episodes import IsolatedRecordExecution, IsolatedSecurityExecution
    from cutback.evals.experiments import ExperimentPlan

    raw = isolated_plan(uuid4(), uuid4(), uuid4(), "sha256:" + "a" * 64).model_dump(mode="json")
    scope = raw["episode_execution"]["isolated_tools"]
    with pytest.raises(ValueError):
        IsolatedRecordExecution.model_validate({**scope, "max_sessions": 129})
    larger = IsolatedSecurityExecution.model_validate(
        {
            **scope,
            "version": "isolated-record-security-tools-v1",
            "max_sessions": 1000,
            "max_total_tool_calls": 100000,
        }
    )
    raw["episode_execution"]["isolated_tools"] = larger.model_dump(mode="json")
    with pytest.raises(ValueError, match="LARGER_SECURITY_BOUNDS"):
        ExperimentPlan.model_validate(raw)
    raw["evaluator_version"] = "deterministic-v9"
    raw["episode_execution"].update(
        version="native-isolated-record-security-v1",
        security_suite={"suite": suite().security_suite.model_dump(mode="json")},
    )
    plan = ExperimentPlan.model_validate(raw)
    assert plan.episode_execution.isolated_tools.max_sessions == 1000
    assert plan.episode_execution.isolated_tools.max_total_tool_calls == 100000


def test_missing_required_attack_categories_cannot_pass_a_large_good_subset():
    spec = SecurityEvaluationSpec(suite=suite().security_suite)
    rows = [row(str(index), arm) for index in range(200) for arm in ("benign", "adversarial")]
    result = summarize_security(spec, rows)
    assert result["gate"] == "insufficient_evidence"
    assert len(result["not_exercised_categories"]) == 6
