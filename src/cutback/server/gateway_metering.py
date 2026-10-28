"""Gateway-owned canonical metadata. Request/response bodies never enter these rows."""

import re
import secrets
from datetime import UTC, datetime

from cutback.core.models import LLMEvent
from cutback.gateway.contracts import LUNA_CONTEXT_TOKENS
from cutback.pricing import Catalog
from cutback.server.db import StoredEvent

TRACE_PARENT = re.compile(r"^00-([a-f0-9]{32})-([a-f0-9]{16})-([a-f0-9]{2})$")


def placeholder(call, environment, *, traceparent=None):
    match = TRACE_PARENT.fullmatch(traceparent or "")
    trace, parent = secrets.token_hex(16), None
    if match and int(match[1], 16) and int(match[2], 16):
        trace, parent = match[1], match[2]
    return LLMEvent(
        event_schema_version="1.2",
        sampling_policy_id="gateway-native-text-v1",
        event_id=call.event_id,
        occurred_at=call.created_at,
        environment=environment,
        service_name="cutback-gateway",
        sdk_version="cutback-gateway-v1",
        trace_id=trace,
        span_id=secrets.token_hex(8),
        parent_span_id=parent,
        request_attempt_id=call.operation_id,
        logical_request_id=call.operation_id,
        provider="openai",
        sdk_surface="responses.create",
        requested_model="gpt-6-luna",
        traffic_kind=call.traffic_kind,
        duration_ms=None,
        status="incomplete",
    )


def response_event(before: LLMEvent, model, body, duration_ms, status_code, *, output_cap=None):
    """Strict native usage, actual tier/model and text-only output qualification."""
    from pydantic import ValidationError

    from cutback.instrumentation.openai import normalize_usage

    values = before.model_dump(mode="python")
    values.update(duration_ms=duration_ms, http_status=status_code)
    code = None
    if status_code != 200 or not isinstance(body, dict):
        values.update(status="error" if status_code >= 400 else "incomplete")
        code = "GATEWAY_UPSTREAM_RESPONSE_BILLING_UNCERTAIN"
    else:
        values.update(normalize_usage(body, "responses.create"))
        values["event_schema_version"] = "1.2"
        values["response_model"] = body.get("model")
        values["service_tier"] = body.get("service_tier")
        values["status"] = "success" if body.get("status") == "completed" else "incomplete"
        output = body.get("output")
        supported = (
            isinstance(output, list)
            and len(output) <= 1024
            and all(
                isinstance(item, dict)
                and item.get("type") in {"message", "reasoning"}
                and (
                    item.get("type") == "reasoning"
                    or (
                        isinstance(item.get("content"), list)
                        and len(item["content"]) <= 1024
                        and all(
                            isinstance(block, dict)
                            and block.get("type") in {"output_text", "refusal"}
                            for block in item["content"]
                        )
                    )
                )
                for item in output
            )
        )
        values["metering_status"] = "text_only" if supported else "unsupported_units"
    try:
        event = LLMEvent.model_validate(values)
    except (ValidationError, TypeError, ValueError):
        event = before.model_copy(update={"duration_ms": duration_ms, "http_status": status_code})
        code = "GATEWAY_MALFORMED_USAGE_BILLING_UNCERTAIN"
    cost = Catalog().price(event)
    if (
        event.response_model != model.model
        or event.metering_status != "text_only"
        or event.service_tier != "default"
        or event.input_tokens is not None
        and event.input_tokens > LUNA_CONTEXT_TOKENS
        or event.output_tokens is not None
        and event.output_tokens > (output_cap or model.max_output_tokens)
        or cost.snapshot_id != model.price_contract.price_snapshot_id
    ):
        return event, None, code or "GATEWAY_ACTUAL_RESPONSE_OUTSIDE_APPROVED_CONTRACT"
    return event, cost.amount, code or ("GATEWAY_USAGE_UNPRICED" if cost.amount is None else None)


def persist_event(session, call, event: LLMEvent, amount, *, allow_insert=True):
    if (
        event.event_id != call.event_id
        or event.traffic_kind != call.traffic_kind
        or event.request_attempt_id != call.operation_id
        or event.event_schema_version != "1.2"
        or event.sampling_policy_id != "gateway-native-text-v1"
    ):
        raise ValueError("GATEWAY_CANONICAL_EVENT_SCOPE_MISMATCH")
    row = (
        session.query(StoredEvent)
        .filter_by(project_id=call.project_id, event_id=call.event_id)
        .one_or_none()
    )
    if row is None:
        if not allow_insert:
            # The corresponding raw observation has expired. Do not reinsert it
            # beside an already retained daily summary and double count spend.
            return
        row = StoredEvent(
            event_id=call.event_id,
            project_id=call.project_id,
            environment_id=call.environment_id,
            occurred_at=event.occurred_at,
            provider="openai",
            price_snapshot_id="",
            cost_status="unpriced",
            cost_breakdown={},
            event={},
        )
        session.add(row)
    elif row.environment_id != call.environment_id or row.event.get("sampling_policy_id") != (
        "gateway-native-text-v1"
    ):
        raise ValueError("GATEWAY_CANONICAL_EVENT_SCOPE_MISMATCH")
    row.model_actual = event.response_model or event.requested_model
    row.workflow_id = event.workflow_id
    row.event = event.model_dump(mode="json")
    row.cost_usd = amount
    row.cost_status = "list_rate" if amount is not None else "unpriced"
    row.cost_reason = None if amount is not None else "gateway_reservation_or_usage_uncertain"
    row.price_snapshot_id = "openai-text-2026-10-28-v1"
    cost = Catalog().price(event)
    row.cost_breakdown = cost.breakdown if amount is not None else {}
    row.received_at = datetime.now(UTC)
