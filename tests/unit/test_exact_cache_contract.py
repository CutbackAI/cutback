from decimal import Decimal

import pytest
from pydantic import ValidationError

from cutback.core.exact_cache import ExactCacheExecution


def cache_spec(**changes):
    return ExactCacheExecution.model_validate(
        {
            "freshness_ref": "owned-immutable-fixture-v1",
            "ttl_seconds": 60,
            "content_retention_authorized": True,
            "immutable_or_safely_invalidated": True,
            "operation_cost_upper_usd": "0.000000001",
            "operation_cost_basis_ref": "owned-fixture-compute-bound",
            **changes,
        }
    )


def test_cache_contract_preserves_money_and_requires_run_isolation():
    spec = cache_spec()
    assert spec.operation_cost_upper_usd == Decimal("0.000000001")
    assert spec.namespace == "isolated_experiment_run"
    assert ExactCacheExecution.model_validate_json(spec.model_dump_json()) == spec


@pytest.mark.parametrize("value", [False, 1, "true", None])
@pytest.mark.parametrize(
    "field", ["content_retention_authorized", "immutable_or_safely_invalidated"]
)
def test_cache_authority_cannot_be_coerced(field, value):
    with pytest.raises(ValidationError):
        cache_spec(**{field: value})


@pytest.mark.parametrize("value", [True, 0.1, float("nan"), "NaN", "-1", "0.0000000000001"])
def test_cache_cost_rejects_ambiguous_or_unbounded_money(value):
    with pytest.raises(ValidationError):
        cache_spec(operation_cost_upper_usd=value)


def test_exact_cache_adds_both_operation_bounds_before_a_provider_reservation():
    from cutback.evals.experiments import ModelSpec, upper_cost

    baseline = ModelSpec(key="baseline", provider="openai", model="gpt-4.1")
    assert "exact_cache" not in baseline.model_dump(mode="json")
    cached = ModelSpec.model_validate(
        {
            **baseline.model_dump(mode="json"),
            "key": "cache",
            "exact_cache": cache_spec(operation_cost_upper_usd="0.1").model_dump(mode="json"),
        }
    )
    assert upper_cost(cached) == upper_cost(baseline) + Decimal("0.2")


@pytest.mark.parametrize(
    "change",
    [
        {"provider": "anthropic", "model": "claude-haiku-4-5"},
        {"provider": "local", "model": "owned-local", "local_cost_upper_usd": "0.1"},
        {
            "request_contract": {
                "sdk_surface": "chat.completions.create",
                "parameters": ["text"],
                "payload": {"op": "parameter", "name": "text"},
            }
        },
    ],
)
def test_exact_cache_cannot_expand_the_executor_support_matrix(change):
    from cutback.evals.experiments import ModelSpec

    with pytest.raises(ValidationError, match="QUALIFIED_STATELESS_NATIVE_TEXT"):
        ModelSpec.model_validate(
            {
                "key": "cache",
                "provider": "openai",
                "model": "gpt-4.1",
                "exact_cache": cache_spec().model_dump(mode="json"),
                **change,
            }
        )
