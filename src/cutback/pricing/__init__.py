"""Immutable offline prices. Public list prices are modeled costs, never invoices."""

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from importlib.resources import files
from pathlib import Path

from cutback.core.models import LLMEvent


@dataclass(frozen=True)
class Cost:
    amount: Decimal | None
    status: str
    reason: str | None
    snapshot_id: str
    breakdown: dict[str, str]


class Catalog:
    def __init__(self):
        configured = os.getenv("CUTBACK_PRICE_CATALOG")
        if configured:
            path = Path(configured)
            if path.stat().st_size > 2097152:
                raise ValueError("pricing snapshot exceeds limit")
            raw = path.read_bytes()
            expected = os.environ["CUTBACK_PRICE_SHA256"]
        else:
            raw = files(__package__).joinpath("catalog.json").read_bytes()
            expected = files(__package__).joinpath("catalog.sha256").read_text().strip()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("pricing snapshot integrity check failed")
        self.data = json.loads(raw)
        from cutback.pricing.importer import validate_snapshot

        validated = validate_snapshot(raw, expected)
        self.data = validated.model_dump(mode="json", exclude_none=True)
        self.snapshot_id = self.data["snapshot_id"]
        self.sha256 = expected

    def rate_info(self, provider: str, model: str | None):
        rate = next(
            (
                r
                for r in self.data["rates"]
                if r["provider"] == provider and model in [r["model"], *r["aliases"]]
            ),
            None,
        )
        return {
            "status": "known_list_rate" if rate else "unknown_price",
            "snapshot_id": self.snapshot_id,
            "rate": rate,
            "checked_at": self.data["checked_at"],
            "currency": "USD",
            "units": "per_million_tokens",
            "reason": None if rate else "no_exact_rate",
            "next_action": "validate_rate_and_task_compatibility_before_experiment",
        }

    def price(self, event: LLMEvent, override: dict | None = None) -> Cost:
        snapshot = override["snapshot_id"] if override else self.snapshot_id

        def unknown(reason):
            return Cost(None, "unpriced", reason, snapshot, {})

        rate = (
            override
            or self.rate_info(event.provider, event.response_model or event.requested_model)["rate"]
        )
        if not rate:
            return unknown("no_exact_rate")
        if event.occurred_at < datetime.fromisoformat(rate["effective_from"]):
            return unknown("outside_rate_effective_period")
        if not override and (
            event.region
            or event.deployment
            or event.service_tier not in {None, "default", "auto", "standard_only"}
        ):
            return unknown("unsupported_region_deployment_or_tier")
        if event.status != "success":
            return unknown("failed_or_incomplete_call_may_be_billable")
        if event.metering_status != "text_only":
            return unknown("unsupported_or_unknown_metering_units")
        if event.input_tokens is None or event.output_tokens is None:
            return unknown("missing_usage")
        if event.cache_write_tokens not in {None, 0} and not rate.get("cache_write"):
            return unknown("cache_write_rate_unavailable")
        if event.cache_read_tokens is None:
            return unknown("cache_usage_unavailable")
        if event.provider == "anthropic" and event.cache_write_tokens is None:
            return unknown("cache_write_duration_unavailable")
        if event.cache_write_1h_tokens and not rate.get("cache_write_1h"):
            return unknown("cache_write_1h_rate_unavailable")
        cached = event.cache_read_tokens
        # OpenAI input is inclusive; reasoning is already included in output.
        buckets = {
            "uncached_input": (
                event.input_tokens - cached if event.provider == "openai" else event.input_tokens,
                rate["input"],
            ),
            "cached_input": (cached, rate["cache_read"]),
            "output": (event.output_tokens, rate["output"]),
        }
        if event.cache_write_tokens:
            buckets["cache_write"] = (event.cache_write_tokens, rate["cache_write"])
        if event.cache_write_1h_tokens:
            buckets["cache_write_1h"] = (event.cache_write_1h_tokens, rate["cache_write_1h"])
        breakdown = {
            key: str(Decimal(tokens) * Decimal(price) / Decimal(1_000_000))
            for key, (tokens, price) in buckets.items()
        }
        status = (
            "measured_usage_modeled_price"
            if event.usage_source == "provider_reported"
            else "estimated_usage_modeled_price"
        )
        return Cost(
            sum((Decimal(v) for v in breakdown.values()), Decimal(0)),
            status,
            None,
            snapshot,
            breakdown,
        )
