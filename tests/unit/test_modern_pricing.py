"""Independent arithmetic and immutable released contracts, with no provider requests."""

import hashlib
from decimal import Decimal, localcontext
from importlib.resources import files
from uuid import UUID

import pytest
from pydantic import ValidationError

from cutback.core.models import LLMEvent
from cutback.core.request_profiles import fingerprint
from cutback.evals.experiments import ModelSpec, qualified_native_model
from cutback.pricing import Catalog
from cutback.pricing.importer import CatalogSnapshot


def legacy():
    return LLMEvent(
        event_id=UUID(int=1),
        request_attempt_id=UUID(int=2),
        occurred_at="2026-10-28T00:00:00Z",
        environment="staging",
        service_name="synthetic-version-contract",
        sdk_version="0.1.0.dev5",
        provider="openai",
        sdk_surface="responses.create",
        requested_model="gpt-4.1-mini",
        response_model="gpt-4.1-mini-2025-04-14",
        trace_id="a" * 32,
        span_id="b" * 16,
        duration_ms=1,
        status="success",
        input_tokens=1000,
        output_tokens=100,
        cache_read_tokens=200,
        reasoning_tokens=20,
        usage_source="provider_reported",
        metering_status="text_only",
    )


def modern(**updates):
    return LLMEvent.model_validate(
        {
            **legacy().model_dump(mode="json"),
            "event_schema_version": "1.1",
            "requested_model": "gpt-6-luna",
            "response_model": "gpt-6-luna",
            "input_tokens": 12000,
            "output_tokens": 100,
            "cache_read_tokens": 4000,
            "cache_write_tokens": 7000,
            "cache_write_30m_tokens": 5000,
            "cache_write_12h_tokens": 2000,
            **updates,
        }
    )


def test_released_event_bytes_and_execution_snapshot_are_unchanged():
    value = legacy()
    assert (
        fingerprint(value.model_dump(mode="json"))
        == "ddbbf5dd6caf07fbb8892a0469dac2f7ce91678059288fb2a816c886bc1c53c3"
    )
    assert "cache_write_12h_tokens" not in value.model_dump(mode="json")
    catalog = Catalog()
    assert catalog.sha256 == "57acf5108329471b4280a09c65714aeb235542effa7bcf93fa16434f301e897d"
    assert catalog.snapshot_id == "public-text-2026-10-24-v2"
    assert catalog.price(value).amount == Decimal("0.0005")


def test_inclusive_cache_writes_are_disjoint_not_an_additive_fee():
    cost = Catalog().price(modern())
    # 1000 ordinary*.10 + 4000 read*.01 + 5000 short*.125 + 2000 long*.25 + 100 out*.50
    assert cost.amount == Decimal("0.001315")
    assert cost.snapshot_id == "openai-text-2026-10-28-v1"
    assert {key: Decimal(value) for key, value in cost.breakdown.items()} == {
        "uncached_input": Decimal("0.0001"),
        "cached_input": Decimal("0.00004"),
        "cache_write_30m": Decimal("0.000625"),
        "cache_write_12h": Decimal("0.0005"),
        "output": Decimal("0.00005"),
    }
    assert sum(map(Decimal, cost.breakdown.values())) == cost.amount


def test_caller_decimal_context_cannot_round_price_buckets():
    with localcontext() as caller:
        caller.prec = 2
        cost = Catalog().price(modern())
        assert cost.amount == Decimal("0.001315")
        assert cost.breakdown["cache_write_30m"] == "0.000625"
        assert caller.prec == 2


def test_legacy_openai_override_cannot_add_cache_write_fees_to_inclusive_input():
    rate = {
        "snapshot_id": "synthetic-legacy-override",
        "effective_from": "2026-10-01T00:00:00+00:00",
        "input": "1",
        "output": "2",
        "cache_read": "0.1",
        "cache_write": "1.25",
    }
    cost = Catalog().price(legacy().model_copy(update={"cache_write_tokens": 10}), rate)
    assert cost.amount is None and cost.reason == "openai_cache_write_contract_unqualified"


@pytest.mark.parametrize("tokens,expected", [(272000, "0.02725"), (272001, "0.0544752")])
def test_long_context_threshold_prices_the_full_request(tokens, expected):
    value = modern(
        input_tokens=tokens,
        cache_read_tokens=0,
        cache_write_tokens=0,
        cache_write_30m_tokens=0,
        cache_write_12h_tokens=0,
    )
    assert Catalog().price(value).amount == Decimal(expected)


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"cache_write_tokens": None}, "modern_cache_write_usage_unavailable"),
        ({"cache_write_12h_tokens": None}, "modern_cache_write_duration_unavailable"),
        ({"cache_write_12h_tokens": 2001}, "modern_usage_partition_invalid"),
        ({"cache_write_tokens": 9000}, "modern_usage_partition_invalid"),
        ({"service_tier": "fast"}, "unsupported_region_deployment_or_tier"),
        ({"region": "eu"}, "unsupported_region_deployment_or_tier"),
        ({"status": "incomplete"}, "failed_or_incomplete_call_may_be_billable"),
    ],
)
def test_modern_unknowns_and_invalid_partitions_cannot_become_zero(updates, reason):
    # Bypass validation deliberately: pricing also defends against invalid internal copies.
    value = modern().model_copy(update=updates)
    cost = Catalog().price(value)
    assert cost.amount is None and cost.reason == reason


def test_known_zero_total_writes_needs_no_guessed_lifetime():
    value = modern(
        event_schema_version="1.0",
        input_tokens=1000,
        cache_read_tokens=200,
        cache_write_tokens=0,
        cache_write_30m_tokens=None,
        cache_write_12h_tokens=None,
    )
    assert Catalog().price(value).amount == Decimal("0.000132")


@pytest.mark.parametrize(
    "updates",
    [
        {"event_schema_version": "1.0"},
        {"provider": "anthropic"},
        {"cache_write_12h_tokens": 2001},
        {"cache_write_30m_tokens": True},
        {"input_tokens": 10000},
    ],
)
def test_extended_wire_contract_rejects_wrong_version_or_inconsistent_usage(updates):
    with pytest.raises(ValidationError):
        modern(**updates)


def test_operator_catalog_does_not_fall_back_to_unselected_bundled_prices(monkeypatch, tmp_path):
    raw = files("cutback.pricing").joinpath("catalog.json").read_bytes()
    path = tmp_path / "owned-prices.json"
    path.write_bytes(raw)
    monkeypatch.setenv("CUTBACK_PRICE_CATALOG", str(path))
    monkeypatch.setenv("CUTBACK_PRICE_SHA256", hashlib.sha256(raw).hexdigest())
    catalog = Catalog()
    assert catalog.rate_info("openai", "gpt-6-luna")["rate"] is None
    assert catalog.price(modern()).amount is None
    assert len(catalog.observation_catalogs()) == 1


def test_observation_rates_do_not_authorize_unqualified_billable_execution():
    assert Catalog().rate_info("openai", "gpt-6-luna")["rate"] is not None
    with pytest.raises(ValueError, match="MODEL_CAPABILITY_CONTRACT_NOT_VALIDATED"):
        qualified_native_model(ModelSpec(key="owned", provider="openai", model="gpt-6-luna"))


@pytest.mark.parametrize("damage", ["old_schema", "provider", "missing_rate", "old_duration"])
def test_modern_price_import_requires_the_complete_versioned_contract(damage):
    raw = files("cutback.pricing").joinpath("openai-text-2026-10-28-v1.json").read_bytes()
    value = CatalogSnapshot.model_validate_json(raw).model_dump(mode="json", exclude_none=True)
    if damage == "old_schema":
        value["schema_version"] = "1.0"
    elif damage == "provider":
        value["rates"][0]["provider"] = "anthropic"
    elif damage == "missing_rate":
        value["rates"][0].pop("cache_write_12h")
    else:
        value["rates"][0]["cache_write_1h"] = "0.2"
    with pytest.raises(ValidationError):
        CatalogSnapshot.model_validate(value)
