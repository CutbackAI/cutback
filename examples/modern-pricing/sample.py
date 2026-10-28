"""Independent synthetic metadata and arithmetic. No network, payload or provider key."""

from datetime import UTC, datetime
from decimal import Decimal

from cutback.core.models import LLMEvent
from cutback.pricing import Catalog

event = LLMEvent(
    event_schema_version="1.1",
    occurred_at=datetime(2026, 10, 28, tzinfo=UTC),
    environment="staging",
    service_name="synthetic-pricing-example",
    provider="openai",
    sdk_surface="responses.create",
    requested_model="gpt-6-luna",
    response_model="gpt-6-luna",
    trace_id="a" * 32,
    span_id="b" * 16,
    duration_ms=1,
    status="success",
    input_tokens=12000,
    output_tokens=100,
    cache_read_tokens=4000,
    cache_write_tokens=7000,
    cache_write_30m_tokens=5000,
    cache_write_12h_tokens=2000,
    reasoning_tokens=0,
    usage_source="provider_reported",
    metering_status="text_only",
    service_tier="default",
)
catalog = Catalog()
cost = catalog.price(event)
assert cost.amount == Decimal("0.001315")
print("Synthetic modeled USD:", cost.amount)
for bucket, amount in cost.breakdown.items():
    print(f"  {bucket}: {amount}")
partial = event.model_copy(update={"cache_write_12h_tokens": None})
unknown = catalog.price(partial)
assert unknown.amount is None
print("Missing lifetime: unknown USD;", unknown.reason)
