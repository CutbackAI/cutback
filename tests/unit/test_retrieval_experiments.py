"""Real retrieval changes reach the native executor under the existing approval ledger."""

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError
from support_retrieval import NativeBoundary, fixture

from cutback.evals.datasets import digest
from cutback.evals.experiments import (
    ExperimentPlan,
    plan_summary,
    run,
)
from cutback.server.plan_artifacts import public_config


def test_candidates_execute_different_real_contexts_and_reject_small_but_wrong_retrieval():
    plan, dataset = fixture()
    executor, retained = NativeBoundary(), []
    approval = plan_summary(plan, dataset)["plan_hash"]
    report = run(
        plan,
        dataset,
        executor,
        approval_hash=approval,
        independent_labels=True,
        record_case=retained.append,
    )
    assert report["status"] == "COMPLETED"
    assert len(executor.inputs) == report["requests"] == 9
    candidates = {r["candidate"]["key"]: r for r in report["recommendations"]}
    assert candidates["compact"]["retrieval_automation_eligible"] is True
    assert candidates["unsafe"]["retrieval_automation_eligible"] is False
    assert candidates["unsafe"]["quality_gate_passed"] is False
    assert report["retrieval"]["full_task_cost_known"] is False
    assert (
        report["retrieval"]["models"]["baseline"]["context_bytes"]
        > report["retrieval"]["models"]["compact"]["context_bytes"]
    )
    assert retained[0]["retrieval"]["documents"][0]["text"]
    assert "fourteen-days" not in json.dumps(public_config(report))
    assert "fourteen-days" not in json.dumps(public_config(plan.model_dump(mode="json")))


def test_unknown_cpu_cost_abstains_without_erasing_known_native_cost():
    plan, dataset = fixture(rate=None)
    report = run(
        plan,
        dataset,
        NativeBoundary(),
        approval_hash=plan_summary(plan, dataset)["plan_hash"],
        independent_labels=True,
    )
    assert report["status"] == "COMPLETED" and report["billing_complete"]
    assert Decimal(report["known_spend_subtotal_usd"]) > 0
    assert all(
        not r["retrieval_automation_eligible"] and r["cheaper_on_sample"] is None
        for r in report["recommendations"]
    )


def test_unknown_native_usage_and_revocation_do_not_start_another_call():
    plan, dataset = fixture()
    executor = NativeBoundary(unknown=True)
    report = run(plan, dataset, executor, approval_hash=plan_summary(plan, dataset)["plan_hash"])
    assert report["status"] == "FAILED" and len(executor.inputs) == 1
    assert not report["billing_complete"] and not report["recommendations"]
    executor = NativeBoundary()

    def revoked():
        raise ValueError("SYNTHETIC_POLICY_REVOKED")

    report = run(
        plan,
        dataset,
        executor,
        approval_hash=plan_summary(plan, dataset)["plan_hash"],
        check_authority=revoked,
    )
    assert report["status"] == "FAILED" and report["requests"] == 0 and not executor.inputs


def test_wrong_approval_or_input_bound_cannot_dispatch():
    plan, dataset = fixture()
    executor = NativeBoundary()
    with pytest.raises(ValueError, match="frozen plan approval"):
        run(plan, dataset, executor, approval_hash="0" * 64)
    dataset["items"][0]["case"]["input"] = "x" * 1025
    report = run(plan, dataset, executor, approval_hash=plan_summary(plan, dataset)["plan_hash"])
    assert report["status"] == "FAILED" and not executor.inputs


@pytest.mark.parametrize(
    "field,value", [("content_nonce", None), ("evaluator_version", "deterministic-v3")]
)
def test_retrieval_cannot_silently_widen_an_existing_approval(field, value):
    plan, _ = fixture()
    raw = plan.model_dump(mode="json")
    raw[field] = value
    with pytest.raises(ValidationError):
        ExperimentPlan.model_validate(raw)


def test_legacy_plan_serialization_still_omits_new_optional_content():
    plan, _ = fixture()
    raw = plan.model_dump(mode="json")
    raw.pop("retrieval_execution")
    raw["evaluator_version"] = "deterministic-v3"
    legacy = ExperimentPlan.model_validate(raw)
    assert "retrieval_execution" not in legacy.model_dump(mode="json")
    assert digest(legacy.model_dump(mode="json")) == digest(raw)
