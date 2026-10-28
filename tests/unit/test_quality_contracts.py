from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from cutback.server.quality_contracts import QualityRuleInput, QualityTarget


def quality_body():
    now = datetime.now(UTC)
    return dict(
        version_id=uuid4(),
        key="synthetic-quality",
        expected_revision=0,
        environment="staging",
        workflow="synthetic-support",
        source_policy_id=uuid4(),
        evaluator="nonempty_output",
        starts_at=now,
        expires_at=now + timedelta(days=1),
        max_evaluations=10,
        approval_ref="owned-synthetic-quality",
    )


def test_online_content_contract_requires_separate_explicit_authority_and_fixed_price():
    body = quality_body()
    with pytest.raises(ValueError, match="EXPLICIT_ONLINE_CONTENT_USE"):
        QualityRuleInput(**body)
    accepted = QualityRuleInput(**body, content_use_authorized=True)
    assert accepted.max_requests == 0 and accepted.max_total_usd == 0
    assert accepted.record_input_features is False
    with pytest.raises(ValueError, match="DETERMINISTIC_QUALITY_PROFILE"):
        QualityRuleInput(**body, content_use_authorized=True, max_requests=1)
    with pytest.raises(ValueError, match="PINNED_PROMPT"):
        QualityRuleInput(**{**body, "evaluator": "output_schema"}, content_use_authorized=True)
    with pytest.raises(ValueError, match="PRICE_CONTRACT_CHANGED"):
        QualityRuleInput(**body, content_use_authorized=True, price_sha256="0" * 64)


def test_online_judging_cannot_bypass_native_model_surface_and_request_bounds():
    body = dict(
        quality_body(),
        content_use_authorized=True,
        evaluator="model_judge",
        prompt_version_id=uuid4(),
    )
    target = dict(provider="openai", model="gpt-4.1-mini", surface="responses.create")
    with pytest.raises(ValueError, match="REQUEST_CAP_REQUIRED"):
        QualityRuleInput(**body, judge=target)
    with pytest.raises(ValueError, match="BUDGET_BELOW"):
        QualityRuleInput(**body, judge=target, max_requests=1)
    rule = QualityRuleInput(**body, judge=target, max_requests=1, max_total_usd="1")
    assert rule.judge.model_spec().request_contract is None
    with pytest.raises(ValueError):
        QualityTarget(**{**target, "model": "unqualified-synthetic-model"})
    with pytest.raises(ValueError, match="SURFACE_UNSUPPORTED"):
        QualityTarget(**{**target, "surface": "messages.create"})
    with pytest.raises(ValueError, match="LOCAL_COMPUTE_COST_BOUND_REQUIRED"):
        QualityTarget(provider="local", model="owned-synthetic", surface="chat.completions.create")
