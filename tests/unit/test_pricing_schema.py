from decimal import Decimal

import pytest
from pydantic import ValidationError

from cutback.core.models import LLMEvent
from cutback.pricing import Catalog


def test_cache_partition_reasoning_subset_decimal(event):
    cost = Catalog().price(event)
    assert cost.amount == Decimal("0.0005")  # 800*.4 + 200*.1 + 100*1.6 per million
    assert cost.status == "measured_usage_modeled_price"
    assert sum(map(Decimal, cost.breakdown.values())) == cost.amount


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"response_model": "unknown"}, "no_exact_rate"),
        ({"input_tokens": None}, "missing_usage"),
        ({"status": "error"}, "failed_or_incomplete_call_may_be_billable"),
        ({"service_tier": "flex"}, "unsupported_region_deployment_or_tier"),
        ({"metering_status": "unsupported_units"}, "unsupported_or_unknown_metering_units"),
        ({"cache_write_tokens": 100}, "cache_write_rate_unavailable"),
    ],
)
def test_unknown_is_not_zero(event, updates, reason):
    cost = Catalog().price(event.model_copy(update=updates))
    assert cost.amount is None
    assert cost.reason == reason


@pytest.mark.parametrize(
    "updates",
    [
        {"prompt": "private"},
        {"raw_usage": {"body": "private"}},
        {"input_tokens": -1},
        {"input_tokens": True},
        {"output_tokens": 1.5},
        {"cache_read_tokens": 1001},
        {"reasoning_tokens": 101},
        {"duration_ms": float("nan")},
        {"event_schema_version": "2.0"},
        {"content_capture_status": "enabled"},
    ],
)
def test_strict_privacy_schema(event, updates):
    with pytest.raises(ValidationError):
        LLMEvent.model_validate({**event.model_dump(), **updates})
