"""Bounded native batch wire protocol. Submission authority belongs to the durable worker.

No filesystem, database, key discovery or network access occurs on import. Batch
turnaround is not per-request latency, and incomplete files never imply zero cost.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from cutback.core.model_identity import reported_model_identifier
from cutback.core.models import LLMEvent
from cutback.evals.datasets import digest
from cutback.evals.experiments import qualified_native_model, upper_cost
from cutback.pricing import Catalog

MAX_REQUESTS = 1000
MAX_INPUT_BYTES = 8 * 1048576
MAX_RESULT_BYTES = 16 * 1048576
MAX_LINE_BYTES = 256000
VERSION = "cutback.openai-batch-responses-text.v1"
MODELS = {
    "gpt-4.1": "gpt-4.1-2025-04-14",
    "gpt-4.1-mini": "gpt-4.1-mini-2025-04-14",
    "gpt-4.1-2025-04-14": "gpt-4.1-2025-04-14",
    "gpt-4.1-mini-2025-04-14": "gpt-4.1-mini-2025-04-14",
}
TERMINAL = {"completed", "failed", "expired", "cancelled"}
STATUSES = TERMINAL | {"validating", "in_progress", "finalizing", "cancelling"}


def validate_model(model):
    qualified_native_model(model)
    if (
        model.provider != "openai"
        or model.model not in MODELS
        or model.native_contract is not None
        or model.region is not None
        or model.routes
        or model.prompt_cache is not None
        or model.request_contract is not None
        or model.agent_turn is not None
    ):
        raise ValueError("BATCH_NATIVE_PLAIN_TEXT_CONTRACT_REQUIRED")


def price_reference(catalog=None):
    """Distinct immutable contract; never silently teach the online catalog batch rates."""
    catalog = catalog or Catalog()
    if not getattr(catalog, "_bundled", False):
        raise ValueError("BATCH_PUBLIC_STANDARD_PRICE_SNAPSHOT_REQUIRED")
    value = {
        "version": VERSION,
        "online_catalog_sha256": catalog.sha256,
        "discount_multiplier": "0.5",
        "models": MODELS,
        "sources": [
            "https://developers.openai.com/api/docs/guides/batch",
            "https://developers.openai.com/api/docs/models/gpt-4.1",
            "https://developers.openai.com/api/docs/models/gpt-4.1-mini",
        ],
        "checked_on": "2026-10-30",
        "units": "USD per native text token; no tools, regional, contract or other fees",
    }
    return {**value, "sha256": digest(value)}


def opaque_id(value):
    try:
        identifier = UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("BATCH_OPAQUE_CALL_ID_REQUIRED") from None
    if str(identifier) != str(value) or identifier.int == 0:
        raise ValueError("BATCH_OPAQUE_CALL_ID_REQUIRED")
    return str(identifier)


def provider_id(value, kind):
    pattern = r"batch_[A-Za-z0-9_-]{1,180}" if kind == "batch" else r"file[-_][A-Za-z0-9_-]{1,180}"
    if type(value) is not str or not re.fullmatch(pattern, value):
        raise ValueError("BATCH_PROVIDER_IDENTIFIER_INVALID")
    return value


def request_body(model, text):
    validate_model(model)
    if type(text) is not str or not 1 <= len(text.encode()) <= 64000:
        raise ValueError("BATCH_TEXT_INPUT_BOUND")
    body = {
        "model": model.model,
        "input": text,
        "max_output_tokens": model.max_output_tokens,
        "store": False,
    }
    if model.instruction:
        body["instructions"] = model.instruction
    # Match the conservative native replay bound, including protocol overhead.
    if len(json.dumps(body, ensure_ascii=False).encode()) + 4096 > model.max_input_tokens:
        raise ValueError("BATCH_INPUT_EXCEEDS_APPROVED_TOKEN_BOUND")
    return body


def input_file(requests):
    """Only opaque reservation IDs leave the server, never customer case identifiers."""
    if not 1 <= len(requests) <= MAX_REQUESTS:
        raise ValueError("BATCH_REQUEST_COUNT_BOUND")
    seen, rows, total, names = set(), [], 0, set()
    for identifier, model, text in requests:
        key = opaque_id(identifier)
        if key in seen:
            raise ValueError("BATCH_DUPLICATE_CALL_ID")
        seen.add(key)
        names.add(model.model)
        line = (
            json.dumps(
                {
                    "custom_id": key,
                    "method": "POST",
                    "url": "/v1/responses",
                    "body": request_body(model, text),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
            + b"\n"
        )
        total += len(line)
        if total > MAX_INPUT_BYTES:
            raise ValueError("BATCH_INPUT_FILE_BYTE_BOUND")
        rows.append(line)
    if len(names) != 1:
        raise ValueError("BATCH_FILE_REQUIRES_ONE_NATIVE_MODEL")
    return b"".join(rows)


@dataclass(frozen=True)
class BatchResult:
    output: str | None = None
    cost_usd: Decimal | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    complete: bool = False
    code: str = "BATCH_RESULT_UNCERTAIN"
    response_model: str | None = None
    # Deliberately no latency field: the provider file does not report that metric.


def parse_response(row, model):
    """Interpret one observed line. Any unqualified evidence keeps the money unknown."""
    validate_model(model)
    response = row.get("response")
    if row.get("error") is not None or not isinstance(response, dict):
        return BatchResult(code="BATCH_PROVIDER_REQUEST_ERROR")
    if type(response.get("status_code")) is not int or response["status_code"] != 200:
        return BatchResult(code="BATCH_PROVIDER_HTTP_ERROR")
    body = response.get("body")
    if (
        not isinstance(body, dict)
        or type(body.get("model")) is not str
        or body["model"] not in {model.model, MODELS[model.model]}
    ):
        return BatchResult(code="BATCH_NATIVE_MODEL_MISMATCH")
    output = body.get("output")
    if (
        body.get("object") != "response"
        or body.get("status") not in ("completed", "incomplete", "failed", "cancelled")
        or not isinstance(output, list)
        or len(output) > 128
    ):
        return BatchResult(code="BATCH_NATIVE_RESPONSE_INVALID")
    unsupported, refusal = bool(body.get("tools")), False
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            unsupported = True
            continue
        if (
            item.get("role") != "assistant"
            or not isinstance(item.get("content"), list)
            or len(item["content"]) > 128
        ):
            return BatchResult(code="BATCH_NATIVE_RESPONSE_INVALID")
        for block in item["content"]:
            if not isinstance(block, dict):
                return BatchResult(code="BATCH_NATIVE_RESPONSE_INVALID")
            if block.get("type") == "refusal":
                refusal = True
            elif block.get("type") != "output_text":
                unsupported = True
            elif type(block.get("text")) is not str or len(block["text"].encode()) > 64000:
                return BatchResult(code="BATCH_NATIVE_RESPONSE_INVALID")
    # Match the pinned SDK's own wire construction. Strict model_validate on
    # SDK3 rejects legacy usage lacking its newer required cache-write field.
    # Native construction preserves absence; raw usage is validated separately
    # below so permissive SDK coercion can never turn strings/bools into money.
    from openai._models import construct_type
    from openai.types.responses import Response

    try:
        native: Any = construct_type(type_=Response, value=body)
        text = native.output_text
    except (ValueError, TypeError, AttributeError):
        return BatchResult(code="BATCH_NATIVE_RESPONSE_INVALID")
    from cutback.instrumentation.openai import normalize_usage

    try:
        event = LLMEvent(
            environment="experiment",
            service_name="cutback-batch-worker",
            provider="openai",
            sdk_surface="responses.create",
            requested_model=model.model,
            response_model=native.model,
            trace_id="1" * 32,
            span_id="1" * 16,
            occurred_at=datetime.now(UTC),
            duration_ms=0,
            status="success" if native.status == "completed" else "incomplete",
            metering_status="unsupported_units" if unsupported else "text_only",
            **normalize_usage(body, "responses.create"),
        )
    except (ValueError, TypeError):
        return BatchResult(
            output=text
            if native.status == "completed" and not unsupported and not refusal
            else None,
            complete=native.status == "completed" and not unsupported and not refusal,
            code="BATCH_NATIVE_USAGE_INVALID",
            response_model=reported_model_identifier(native.model),
        )
    cost = Catalog().price(event).amount
    # The serving mode is certified by the scoped batch job, not by response tier.
    if body.get("service_tier") not in (None, "default", "auto"):
        cost = None
    if (event.input_tokens is not None and event.input_tokens > model.max_input_tokens) or (
        event.output_tokens is not None and event.output_tokens > model.max_output_tokens
    ):
        cost = None
    complete = native.status == "completed" and not unsupported and not refusal
    return BatchResult(
        output=text if complete else None,
        cost_usd=cost * Decimal("0.5") if cost is not None else None,
        input_tokens=event.input_tokens,
        output_tokens=event.output_tokens,
        complete=complete,
        code="BATCH_NATIVE_RESPONSE_OBSERVED",
        response_model=reported_model_identifier(native.model),
    )


def result_files(chunks, expected):
    """Order independent, bounded, strict JSONL; ambiguous IDs never overwrite evidence.

    expected maps opaque canonical call IDs to their frozen ModelSpec. Missing,
    duplicate and unknown lines reject the entire file; callers may not settle
    a prefix or silently discard a later conflicting observation. Missing IDs
    keep their individual reservations unknown, so total billing stays unknown.
    """
    if not 1 <= len(expected) <= MAX_REQUESTS:
        raise ValueError("BATCH_RESULT_REQUEST_COUNT_BOUND")
    for identifier in expected:
        opaque_id(identifier)
    buffer, total, rows = b"", 0, {}

    def decode(line):
        if not line or len(line) > MAX_LINE_BYTES:
            raise ValueError("BATCH_RESULT_LINE_BOUND")

        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("BATCH_DUPLICATE_JSON_KEY")
                result[key] = value
            return result

        try:
            row = json.loads(
                line,
                object_pairs_hook=unique,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
        except (ValueError, TypeError, RecursionError, UnicodeError):
            raise ValueError("BATCH_RESULT_JSON_INVALID") from None
        if not isinstance(row, dict):
            raise ValueError("BATCH_RESULT_OBJECT_REQUIRED")
        identifier = row.get("custom_id")
        if not isinstance(identifier, str) or identifier not in expected or identifier in rows:
            raise ValueError("BATCH_RESULT_ID_UNKNOWN_OR_DUPLICATE")
        rows[identifier] = row

    for chunk in chunks:
        if type(chunk) is not bytes:
            raise ValueError("BATCH_RESULT_BYTES_REQUIRED")
        total += len(chunk)
        if total > MAX_RESULT_BYTES:
            raise ValueError("BATCH_RESULT_FILE_BYTE_BOUND")
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            decode(line.removesuffix(b"\r"))
        if len(buffer) > MAX_LINE_BYTES:
            raise ValueError("BATCH_RESULT_LINE_BOUND")
    if buffer:
        decode(buffer)
    return {
        identifier: parse_response(rows[identifier], model)
        if identifier in rows
        else BatchResult(code="BATCH_RESULT_MISSING")
        for identifier, model in expected.items()
    }


def file_sha256(blob):
    return hashlib.sha256(blob).hexdigest()


def reservation_bound(model):
    # Reserve the full online upper bound. The known batch discount may reduce
    # final accounted cost; it cannot widen the owner-approved dollar envelope.
    validate_model(model)
    return upper_cost(model)
