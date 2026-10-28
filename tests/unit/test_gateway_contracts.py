"""Gateway metadata contracts never imply an enabled proxy or invoice guarantee."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from uuid import uuid4

import pytest
from pydantic import ValidationError

from cutback.core.native_models import NativeModelContract
from cutback.gateway.contracts import (
    GatewayPolicy,
    ModelLimit,
    ResponsesText,
    SpendLimit,
    TeamLimit,
    qualify_model,
    qualify_request,
    upper_cost,
)


def model_for(effort="medium"):
    return ModelLimit(
        price_contract=NativeModelContract.current_luna_text(reasoning_effort=effort),
        limit=SpendLimit(modeled_usd="5", requests=10, concurrent_requests=2),
    )


def policy_for():
    now = datetime.now(UTC)
    limit = SpendLimit(modeled_usd="10", requests=20, concurrent_requests=4)
    return GatewayPolicy(
        policy_id=uuid4(),
        environment_ids=[uuid4()],
        starts_at=now,
        expires_at=now + timedelta(days=1),
        project_limit=limit,
        teams=[TeamLimit(team_id=uuid4(), limit=limit)],
        models=[model_for()],
    )


@pytest.mark.parametrize("value", [True, 1.2, "NaN", "Infinity", "0", "-1", "0.0000000000001"])
def test_spend_limit_rejects_inexact_nonfinite_nonpositive_money(value):
    with pytest.raises(ValidationError):
        SpendLimit(modeled_usd=value, requests=10, concurrent_requests=1)


def test_full_context_reservation_does_not_guess_input_from_request_length():
    model = model_for()
    with localcontext() as context:
        context.prec = 6
        amount = upper_cost(model)
    # Full 1.05M context at the largest inclusive write rate, long-context
    # premiums and Fast premium, plus the complete configured output cap.
    assert amount == Decimal("1.051536")
    request = ResponsesText(
        model="gpt-6-luna", input="short", max_output_tokens=16, store=False, service_tier="default"
    )
    qualify_request(request, model)
    assert upper_cost(model) == amount


@pytest.mark.parametrize("effort", ["none", "low", "medium", "high", "xhigh", "max"])
def test_native_request_must_match_approved_reasoning_and_output_bound(effort):
    model = model_for(effort)
    request = ResponsesText(
        model="gpt-6-luna",
        input="owned synthetic text",
        reasoning={"effort": effort},
        max_output_tokens=1024,
        store=False,
        service_tier="default",
    )
    qualify_request(request, model)
    with pytest.raises(ValueError, match="OUTSIDE_APPROVED"):
        qualify_request(request.model_copy(update={"max_output_tokens": 2048}), model)
    other = "low" if effort != "low" else "none"
    with pytest.raises(ValueError, match="OUTSIDE_APPROVED"):
        qualify_request(
            request.model_copy(
                update={"reasoning": request.reasoning.model_copy(update={"effort": other})}
            ),
            model,
        )


@pytest.mark.parametrize(
    "change",
    [
        {"store": 0},
        {"stream": 0},
        {"store": True},
        {"stream": True},
        {"service_tier": "auto"},
        {"tools": []},
        {"previous_response_id": "resp_other"},
        {"input": [{"role": "user", "content": "not the qualified wire shape"}]},
        {"reasoning": {"effort": "medium", "summary": "auto"}},
        {"prompt_cache_options": {"ttl": "12h"}},
        {"max_output_tokens": True},
    ],
)
def test_unqualified_wire_options_fail_before_dispatch(change):
    values = dict(
        model="gpt-6-luna",
        input="synthetic",
        max_output_tokens=16,
        store=False,
        service_tier="default",
    )
    with pytest.raises(ValidationError):
        ResponsesText.model_validate({**values, **change})


def test_policy_hash_freezes_all_scopes_prices_limits_and_window():
    policy = policy_for()
    assert GatewayPolicy.model_validate_json(policy.model_dump_json()).approval_hash() == (
        policy.approval_hash()
    )
    for update in (
        {"environment_ids": [uuid4()]},
        {"expires_at": policy.expires_at + timedelta(seconds=1)},
        {"project_limit": policy.project_limit.model_copy(update={"modeled_usd": Decimal("9")})},
    ):
        assert policy.model_copy(update=update).approval_hash() != policy.approval_hash()
    wrong = model_for()
    wrong = wrong.model_copy(
        update={
            "price_contract": wrong.price_contract.model_copy(update={"price_sha256": "0" * 64})
        }
    )
    with pytest.raises(ValueError, match="REAPPROVAL_REQUIRED"):
        qualify_model(wrong)


def test_policy_rejects_unbounded_duplicate_and_cross_limit_contracts():
    policy = policy_for().model_dump(mode="python")
    invalid = [
        {"expires_at": policy["starts_at"] + timedelta(days=32)},
        {"starts_at": policy["starts_at"].replace(tzinfo=None)},
        {"environment_ids": policy["environment_ids"] * 2},
        {"models": policy["models"] * 2},
        {"project_limit": SpendLimit(modeled_usd="1", requests=1, concurrent_requests=1)},
    ]
    for update in invalid:
        with pytest.raises(ValidationError):
            GatewayPolicy.model_validate({**policy, **update})
