"""Coupled variants execute actual retrieval and cannot bypass legacy or quality gates."""

import json
from dataclasses import replace
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from support_retrieval import NativeBoundary, fixture

from cutback.evals.datasets import digest
from cutback.evals.experiments import ExperimentPlan, plan_summary, run
from cutback.evals.joint_retrieval_planning import candidate_pairs, confirmation_plan
from cutback.evals.retrieval_planning import confirmation_plan as legacy_confirmation
from cutback.server.plan_artifacts import public_config


def joint(rate="0.01"):
    prior, dataset = fixture(rate)
    raw = prior.model_dump(mode="json")
    raw["baseline"]["model"] = "gpt-4.1"
    raw["candidates"][0].update(model="gpt-4.1-mini", max_output_tokens=64)
    raw["candidates"][1].update(model="gpt-4.1-mini", max_output_tokens=32)
    raw["evaluator_version"] = "deterministic-v18"
    raw["retrieval_execution"]["version"] = "cutback.joint-model-retrieval.v1"
    return ExperimentPlan.model_validate(raw), dataset


class NativeModels(NativeBoundary):
    """Explicit synthetic costs/answers, not a tokenizer or provider quality measurement."""

    def __init__(self):
        super().__init__()
        self.models = []

    def execute(self, model, text, timeout_seconds):
        self.models.append((model.model, model.max_output_tokens))
        answer = super().execute(model, text, timeout_seconds)
        divisor = {"gpt-4.1": 1, "gpt-4.1-mini": 5}[model.model]
        return replace(
            answer,
            cost_usd=answer.cost_usd / divisor,
            output="wrong" if model.max_output_tokens == 32 else answer.output,
        )


def test_same_questions_reach_distinct_native_variants_and_cheaper_wrong_candidate_is_rejected():
    plan, dataset = joint()
    executor = NativeModels()
    report = run(
        plan,
        dataset,
        executor,
        approval_hash=plan_summary(plan, dataset)["plan_hash"],
        independent_labels=True,
    )
    assert report["status"] == "COMPLETED" and report["billing_complete"]
    assert len(executor.models) == report["requests"] == 9
    assert set(executor.models) == {("gpt-4.1", 128), ("gpt-4.1-mini", 64), ("gpt-4.1-mini", 32)}
    choices = {row["candidate"]["key"]: row for row in report["recommendations"]}
    assert choices["compact"]["joint_model_retrieval_confirmation_eligible"]
    assert set(choices["compact"]["search_axes_changed"]) == {
        "native_model",
        "output_ceiling",
        "retrieval_settings",
    }
    assert choices["unsafe"]["cheaper_on_sample"]
    assert not choices["unsafe"]["joint_model_retrieval_confirmation_eligible"]
    assert report["joint_model_retrieval"]["full_task_cost_known"] is False
    assert "fourteen-days" not in json.dumps(public_config(plan.model_dump(mode="json")))
    confirmed = confirmation_plan(plan, report, str(uuid4()), plan.budget)
    assert confirmed.split == "test" and len(confirmed.candidates) == 1
    assert len(confirmed.retrieval_execution.variants) == 2
    with pytest.raises(ValueError, match="COMPLETED_RETRIEVAL_VALIDATION_REQUIRED"):
        legacy_confirmation(plan, report, str(uuid4()), plan.budget)


@pytest.mark.parametrize("change", ["legacy_version", "instruction", "nonce", "duplicate"])
def test_joint_versions_and_source_invariants_cannot_be_downgraded(change):
    plan, _ = joint()
    raw = plan.model_dump(mode="json")
    if change == "legacy_version":
        raw["evaluator_version"] = "deterministic-v6"
    elif change == "instruction":
        raw["candidates"][0]["instruction"] = "changed unqualified instruction"
    elif change == "nonce":
        raw["content_nonce"] = None
    else:
        raw["candidates"][0] = {**raw["baseline"], "key": "compact"}
        raw["retrieval_execution"]["variants"][1]["settings"] = raw["retrieval_execution"][
            "variants"
        ][0]["settings"]
    with pytest.raises(ValidationError):
        ExperimentPlan.model_validate(raw)


def test_unknown_cpu_cost_preserves_native_subtotal_and_prevents_selection():
    plan, dataset = joint(None)
    report = run(
        plan,
        dataset,
        NativeModels(),
        approval_hash=plan_summary(plan, dataset)["plan_hash"],
        independent_labels=True,
    )
    assert Decimal(report["known_spend_subtotal_usd"]) > 0
    assert all(row["cheaper_on_sample"] is None for row in report["recommendations"])
    with pytest.raises(ValueError, match="NO_JOINT_RETRIEVAL_CANDIDATE"):
        confirmation_plan(plan, report, str(uuid4()), plan.budget)


def test_finite_portfolio_covers_native_only_retrieval_only_and_combined_hypotheses():
    plan, _ = joint()
    settings = plan.retrieval_execution.variants[0].settings
    pairs = candidate_pairs(plan.baseline, settings)
    assert 1 <= len(pairs) <= 5
    assert any(m.model != plan.baseline.model and value == settings for m, value in pairs)
    assert any(m.model == plan.baseline.model and value != settings for m, value in pairs)
    assert any(m.model != plan.baseline.model and value != settings for m, value in pairs)
    assert len(
        {(m.model_dump_json(exclude={"key"}), value.model_dump_json()) for m, value in pairs}
    ) == len(pairs)


def test_legacy_v6_hash_is_identical_to_the_frozen_pre_extension_wheel():
    from cutback.core.retrieval import CONTRACT_SHA256

    plan, _ = fixture()
    plan = plan.model_copy(update={"content_nonce": UUID("00000000-0000-4000-8000-000000000001")})
    wire = plan.model_dump(mode="json")
    assert wire["retrieval_execution"]["contract_sha256"] == CONTRACT_SHA256
    # The recipe deliberately includes the interpreter's Unicode database.
    # Normalize only that fixture metadata to the frozen wheel's Python 3.11
    # recipe when comparing wire-format compatibility across interpreters.
    # Execution still requires the actual local recipe above.
    wire["retrieval_execution"]["contract_sha256"] = (
        "05e5f5852cfb3d64bd0d4f56443424cc2685610975579ec18aac634cb9a6164c"
    )
    assert digest(wire) == "12aa530e7e985112bce17fb1f34d03b1a80cd4de2e4145256b2cae7053464a4c"


def test_synthetic_executor_without_fixture_plan_stays_explicit_in_every_candidate():
    from cutback.evals.joint_retrieval_planning import regression_gate

    plan, dataset = joint()
    executor = NativeModels()
    executor.synthetic_transport = True
    report = run(
        plan,
        dataset,
        executor,
        approval_hash=plan_summary(plan, dataset)["plan_hash"],
        independent_labels=True,
    )
    assert report["synthetic_execution"]["real_model_quality_verified"] is False
    assert all(row["cost_evidence"] == "scripted_fixture_usage" for row in report["models"])
    assert all(
        row["synthetic_execution"]["customer_savings_verified"] is False
        for row in report["recommendations"]
    )
    assert (
        "fixture_transport_cannot_verify_real_model_quality"
        in regression_gate(report, expected_plan_hash=report["plan"]["plan_hash"])["reasons"]
    )


def test_failed_synthetic_run_keeps_provenance_when_authority_stops_dispatch():
    plan, dataset = joint()
    executor = NativeModels()
    executor.synthetic_transport = True

    def blocked(*args, **kwargs):
        raise ValueError("OWNED_AUTHORITY_REVOKED")

    report = run(
        plan,
        dataset,
        executor,
        approval_hash=plan_summary(plan, dataset)["plan_hash"],
        reserve_call=blocked,
        record_call=lambda *args, **kwargs: None,
    )
    assert report["status"] == "FAILED" and executor.models == []
    assert report["synthetic_execution"]["customer_savings_verified"] is False
    assert report["billing_complete"] is False and report["recommendations"] == []
