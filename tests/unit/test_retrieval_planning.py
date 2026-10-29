import json
from decimal import ROUND_DOWN, Decimal, localcontext
from uuid import uuid4

import pytest
from pydantic import ValidationError
from support_retrieval import NativeBoundary, fixture

from cutback.core.retrieval import RetrievalSettings
from cutback.evals.experiments import plan_summary, run
from cutback.evals.retrieval import RetrievalExecutionSpec, RetrievalProcessor
from cutback.evals.retrieval_planning import (
    candidate_settings,
    confirmation_plan,
    draft_retrieval_plan,
)


def test_cpu_money_is_exact_under_hostile_ambient_decimal_context():
    plan, _ = fixture(rate="1000000")
    processor = RetrievalProcessor(plan.retrieval_execution)
    with localcontext() as ambient:
        ambient.prec, ambient.Emax, ambient.Emin, ambient.rounding = 2, 1, -1, ROUND_DOWN
        cost = processor._assumed_cost(1000001)
        assert ambient.prec == 2 and ambient.Emax == 1
    # Two milliseconds: (2/1000)*1,000,000/3600, rounded up to twelve decimal places.
    assert cost == Decimal("0.555555555556")


def test_automatic_search_is_distinct_bounded_and_preserves_native_contract():
    plan, dataset = fixture()
    spec = plan.retrieval_execution
    metadata = {
        "workflow": plan.workflow,
        "hash": plan.dataset_hash,
        "provenance": {"independent_labels": True, "max_input_bytes": 100},
    }
    draft = draft_retrieval_plan(
        metadata,
        plan.baseline,
        spec.corpus,
        spec.variants[0].settings,
        metric=plan.metric,
        budget=plan.budget,
    )
    assert 1 <= len(draft.candidates) <= 5
    assert (
        len(set(v.settings.model_dump_json() for v in draft.retrieval_execution.variants))
        == len(draft.candidates) + 1
    )
    assert all(
        m.model_dump(exclude={"key"}) == plan.baseline.model_dump(exclude={"key"})
        for m in draft.candidates
    )
    assert candidate_settings(RetrievalSettings()) == candidate_settings(RetrievalSettings())
    metadata["provenance"]["independent_labels"] = False
    with pytest.raises(ValueError, match="INDEPENDENT_ANSWER"):
        draft_retrieval_plan(
            metadata,
            plan.baseline,
            spec.corpus,
            spec.variants[0].settings,
            metric=plan.metric,
            budget=plan.budget,
        )


def test_confirmation_selects_validation_winner_and_preserves_encrypted_content():
    plan, dataset = fixture()
    report = run(
        plan,
        dataset,
        NativeBoundary(),
        approval_hash=plan_summary(plan, dataset)["plan_hash"],
        independent_labels=True,
    )
    selected = confirmation_plan(plan, report, str(uuid4()), plan.budget)
    assert selected.split == "test" and len(selected.candidates) == 1
    assert selected.candidates[0].key == "compact"
    assert selected.content_nonce == plan.content_nonce
    assert selected.retrieval_execution.corpus == plan.retrieval_execution.corpus
    assert len(selected.retrieval_execution.variants) == 2
    report["billing_complete"] = False
    with pytest.raises(ValueError, match="COMPLETED_RETRIEVAL"):
        confirmation_plan(plan, report, str(uuid4()), plan.budget)


@pytest.mark.parametrize(
    "value", [True, False, 0.1, "NaN", "Infinity", "-1", "1e-13", "1e7", "invalid"]
)
def test_cpu_assumptions_reject_inexact_nonfinite_out_of_bound_values(value):
    plan, _ = fixture()
    raw = plan.retrieval_execution.model_dump(mode="json")
    raw["assumed_usd_per_cpu_hour"] = value
    with pytest.raises((ValueError, ValidationError)):
        RetrievalExecutionSpec.model_validate(raw)


def test_explicit_zero_and_unknown_cpu_cost_are_distinct_and_serialize_exactly():
    plan, _ = fixture(rate="0")
    assert plan.retrieval_execution.assumed_usd_per_cpu_hour == Decimal(0)
    assert json.loads(plan.retrieval_execution.model_dump_json())["assumed_usd_per_cpu_hour"] == "0"
    plan, _ = fixture(rate=None)
    assert plan.retrieval_execution.assumed_usd_per_cpu_hour is None


def test_equivalent_decimal_spellings_cannot_duplicate_billable_candidates():
    plan, _ = fixture()
    spec = plan.retrieval_execution
    a = RetrievalSettings(chunk_chars=256, overlap_chars=0, top_k=1, k1=Decimal("1.20"))
    b = RetrievalSettings(chunk_chars=256, overlap_chars=0, top_k=1, k1=Decimal("1.2"))
    metadata = {
        "hash": plan.dataset_hash,
        "workflow": plan.workflow,
        "provenance": {"independent_labels": True},
    }
    with pytest.raises(ValueError, match="DISTINCT_AND_BOUNDED"):
        draft_retrieval_plan(
            metadata,
            plan.baseline,
            spec.corpus,
            spec.variants[0].settings,
            metric=plan.metric,
            budget=plan.budget,
            candidates=[a, b],
        )
