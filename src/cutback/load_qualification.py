"""Explicit owned-loopback load qualification. No provider calls or payload capture."""

import asyncio
import hashlib
import json
import math
import platform
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4, uuid5

import httpx
from pydantic import Field, StrictInt, model_validator

from cutback.core.models import LLMEvent, StrictModel
from cutback.core.prompts import fingerprint
from cutback.pricing import Catalog


class LocalLoadProfile(StrictModel):
    contract: Literal["cutback.local-load-profile.v1"] = "cutback.local-load-profile.v1"
    events: Annotated[StrictInt, Field(ge=200, le=50000)] = 10000
    batch_size: Annotated[StrictInt, Field(ge=50, le=200)] = 100
    concurrency: Annotated[StrictInt, Field(ge=1, le=16)] = 4
    query_requests: Annotated[StrictInt, Field(ge=4, le=200)] = 40
    max_workload_seconds: Annotated[StrictInt, Field(ge=10, le=600)] = 300
    minimum_latency_samples: Annotated[StrictInt, Field(ge=4, le=1000)] = 20
    p95_ingest_ms_ceiling: float = Field(
        default=10000, strict=True, gt=0, le=60000, allow_inf_nan=False
    )
    p95_query_ms_ceiling: float = Field(
        default=5000, strict=True, gt=0, le=60000, allow_inf_nan=False
    )
    minimum_unique_events_per_second: float = Field(
        default=10, strict=True, gt=0, le=1000000, allow_inf_nan=False
    )

    @model_validator(mode="after")
    def bounded(self):
        if self.events % (2 * self.batch_size) or self.query_requests % 2:
            raise ValueError("LOAD_PROFILE_REQUIRES_EQUAL_WHOLE_BATCHES_AND_QUERIES_PER_TENANT")
        if min(self.events // self.batch_size, self.query_requests) < self.minimum_latency_samples:
            raise ValueError("LOAD_PROFILE_TOO_FEW_REQUESTS_FOR_DECLARED_LATENCY_SAMPLE_MINIMUM")
        return self


def latency_summary(samples, *, minimum):
    """Nearest-rank descriptive quantiles, only from the retained raw measurements."""
    if (
        type(minimum) is not int
        or minimum < 1
        or minimum > 10000
        or not isinstance(samples, list)
        or len(samples) > 10000
        or any(type(value) is not int or not 0 <= value < 2**63 for value in samples)
    ):
        raise ValueError("LOAD_LATENCIES_REQUIRE_NONNEGATIVE_INTEGER_NANOSECONDS")
    ordered = sorted(samples)
    enough = len(ordered) >= minimum
    return {
        "samples": len(ordered),
        "minimum_samples": minimum,
        "quantile_method": "nearest_rank",
        "p50_ms": ordered[math.ceil(len(ordered) * 0.5) - 1] / 1000000 if enough else None,
        "p95_ms": ordered[math.ceil(len(ordered) * 0.95) - 1] / 1000000 if enough else None,
        "scope": "client_request_wall_time_excludes_client_semaphore_queue",
        "population_coverage": "unknown",
    }


class QualificationFailure(ValueError):
    pass


async def qualify_local(endpoint, admin_token, profile: LocalLoadProfile):
    """Create two synthetic tenants, measure actual HTTP/PG paths, delete own data.

    The caller verifies ownership of the local collector. This function accepts
    only numeric loopback HTTP, closed paths and generated independent fixtures.
    No caller-controlled destination, source import or vendor request exists.
    """
    url = urlsplit(endpoint)
    if (
        url.scheme != "http"
        or url.hostname not in {"127.0.0.1", "::1"}
        or url.username is not None
        or url.password is not None
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
    ):
        raise ValueError("LOAD_QUALIFICATION_REQUIRES_OWNED_NUMERIC_LOOPBACK_HTTP")
    profile = LocalLoadProfile.model_validate(
        profile.model_dump(mode="python") if isinstance(profile, LocalLoadProfile) else profile
    )
    samples: dict[str, list[int]] = {
        key: []
        for key in ("event_ingest", "span_ingest", "duplicate_replay", "trace_query", "usage_query")
    }
    errors: dict[str, int] = {}
    tenants, unknown_creations = [], []
    accepted = duplicates = span_count = 0
    counts_verified = isolation_verified = False
    workload_ns = None
    cleanup = []
    inflight = {"active": 0, "peak": 0}
    now = datetime.now(UTC)
    catalog = Catalog()
    semaphore = asyncio.Semaphore(profile.concurrency)

    async with httpx.AsyncClient(
        base_url=endpoint,
        timeout=30,
        trust_env=False,
        follow_redirects=False,
        limits=httpx.Limits(
            max_connections=profile.concurrency, max_keepalive_connections=profile.concurrency
        ),
    ) as client:

        async def request(
            method, path, *, token=admin_token, body=None, params=None, metric=None, expected=200
        ):
            async with semaphore:
                inflight["active"] += 1
                inflight["peak"] = max(inflight["peak"], inflight["active"])
                started = time.perf_counter_ns()
                try:
                    async with client.stream(
                        method,
                        path,
                        headers={"Authorization": "Bearer " + token},
                        json=body,
                        params=params,
                    ) as response:
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(raw) + len(chunk) > 2097152:
                                raise QualificationFailure("LOAD_RESPONSE_BOUND_EXCEEDED")
                            raw.extend(chunk)
                        if response.status_code != expected:
                            raise QualificationFailure("LOAD_HTTP_" + str(response.status_code))
                        if expected == 403:
                            return None
                        return json.loads(raw)
                except (httpx.HTTPError, json.JSONDecodeError):
                    raise QualificationFailure("LOAD_TRANSPORT_OR_RESPONSE_INVALID") from None
                finally:
                    inflight["active"] -= 1
                    if metric:
                        samples[metric].append(time.perf_counter_ns() - started)

        def fail(code):
            errors[code] = errors.get(code, 0) + 1

        async def complete_group(coroutines):
            tasks = [asyncio.create_task(coroutine) for coroutine in coroutines]
            try:
                return await asyncio.gather(*tasks)
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

        async def ingest_batch(tenant, offset):
            nonlocal accepted, duplicates, span_count
            records, spans = [], []
            batch_known = Decimal(0)
            for index in range(offset, offset + profile.batch_size):
                trace = uuid5(UUID(tenant["id"]), "trace-" + str(index)).hex
                root_span = uuid5(UUID(trace), "root").hex[:16]
                model = "gpt-4.1-mini" if index % 2 else "unknown-synthetic-model"
                event = LLMEvent(
                    environment="staging",
                    service_name="synthetic-load",
                    provider="openai",
                    sdk_surface="responses.create",
                    requested_model=model,
                    response_model=model,
                    trace_id=trace,
                    span_id=uuid5(UUID(trace), "llm").hex[:16],
                    parent_span_id=root_span,
                    logical_request_id=uuid5(UUID(tenant["id"]), "request-" + str(index)),
                    occurred_at=now,
                    duration_ms=10,
                    status="success",
                    input_tokens=100,
                    output_tokens=20,
                    cache_read_tokens=0,
                    reasoning_tokens=0,
                    usage_source="provider_reported",
                    metering_status="text_only",
                )
                cost = catalog.price(event)
                if model == "gpt-4.1-mini":
                    if cost.amount is None:
                        raise QualificationFailure("LOAD_FIXED_KNOWN_MODEL_PRICE_UNAVAILABLE")
                    batch_known += cost.amount
                records.append(event.model_dump(mode="json"))
                start = int(now.timestamp() * 1000000000)
                spans.append(
                    {
                        "trace_id": trace,
                        "span_id": root_span,
                        "operation": "classify",
                        "kind": "chain",
                        "service": "synthetic-load",
                        "workflow": "classify",
                        "release": "synthetic-load-v1",
                        "started_ns": start,
                        "ended_ns": start + 10000000,
                        "status": "unknown",
                    }
                )
            body = {"project_id": tenant["id"], "events": records}
            first = await request(
                "POST",
                "/v1/ingest/events",
                token=tenant["ingest"],
                body=body,
                metric="event_ingest",
            )
            if first["accepted"] != profile.batch_size or first["duplicate"] != 0:
                raise QualificationFailure("LOAD_INGEST_OR_IDEMPOTENCY_RECEIPT_MISMATCH")
            accepted += first["accepted"]
            tenant["expected_known_subtotal"] += batch_known
            replay = await request(
                "POST",
                "/v1/ingest/events",
                token=tenant["ingest"],
                body=body,
                metric="duplicate_replay",
            )
            if replay["accepted"] != 0 or replay["duplicate"] != profile.batch_size:
                raise QualificationFailure("LOAD_INGEST_OR_IDEMPOTENCY_RECEIPT_MISMATCH")
            duplicates += replay["duplicate"]
            traced = await request(
                "POST",
                "/v1/ingest/spans",
                token=tenant["ingest"],
                body={"spans": spans},
                metric="span_ingest",
            )
            if traced["accepted"] != profile.batch_size:
                raise QualificationFailure("LOAD_INGEST_OR_IDEMPOTENCY_RECEIPT_MISMATCH")
            span_count += traced["accepted"]

        async def read_queries(tenant):
            traces = await request(
                "GET",
                tenant["base"] + "/traces",
                token=tenant["viewer"],
                params={"environment_id": tenant["environment"], "limit": 10},
                metric="trace_query",
            )
            usage = await request(
                "GET", tenant["base"] + "/usage", token=tenant["viewer"], metric="usage_query"
            )
            if not traces["items"] or any(
                row["environment_id"] != tenant["environment"] for row in traces["items"]
            ):
                raise QualificationFailure("LOAD_TRACE_QUERY_SCOPE_OR_ROWS_INVALID")
            summary = usage["summary"]
            if (
                summary["observed_attempts"] != profile.events // 2
                or summary["unpriced_requests"] != profile.events // 4
                or summary["modeled_cost_usd"] is not None
            ):
                raise QualificationFailure("LOAD_USAGE_COUNTS_OR_UNKNOWN_COST_LOST")
            if (
                Decimal(summary["known_modeled_cost_subtotal_usd"])
                != tenant["expected_known_subtotal"]
            ):
                raise QualificationFailure("LOAD_KNOWN_COST_SUBTOTAL_MISMATCH")

        try:
            await request("GET", "/v1/health/ready")
            for _ in range(2):
                slug = "synthetic-load-" + uuid4().hex
                unknown_creations.append(slug)
                row = await request("POST", "/v1/projects", body={"slug": slug}, expected=201)
                tenant = {
                    "id": str(UUID(row["id"])),
                    "slug": slug,
                    "base": "/v1/projects/" + row["id"],
                    "expected_known_subtotal": Decimal(0),
                }
                tenants.append(tenant)
                unknown_creations.remove(slug)
                for role in ("ingest", "viewer"):
                    key = await request(
                        "POST", tenant["base"] + "/keys", body={"role": role}, expected=201
                    )
                    tenant[role] = key["token"]
                environments = await request("GET", tenant["base"] + "/environments")
                tenant["environment"] = next(
                    row["id"] for row in environments["items"] if row["name"] == "staging"
                )
            for a, b in ((tenants[0], tenants[1]), (tenants[1], tenants[0])):
                await request("GET", b["base"] + "/usage", token=a["viewer"], expected=403)
                await request(
                    "GET",
                    b["base"] + "/traces",
                    token=a["viewer"],
                    params={"environment_id": b["environment"]},
                    expected=403,
                )
            isolation_verified = True
            async with asyncio.timeout(profile.max_workload_seconds):
                started = time.perf_counter_ns()
                # Limit live coroutine/payload groups as well as actual HTTP concurrency.
                pending = []
                for offset in range(0, profile.events // 2, profile.batch_size):
                    for tenant in tenants:
                        pending.append(ingest_batch(tenant, offset))
                    if len(pending) >= profile.concurrency:
                        await complete_group(pending)
                        pending = []
                if pending:
                    await complete_group(pending)
                workload_ns = time.perf_counter_ns() - started
                pending = []
                for index in range(profile.query_requests):
                    pending.append(read_queries(tenants[index % 2]))
                    if len(pending) >= profile.concurrency:
                        await complete_group(pending)
                        pending = []
                if pending:
                    await complete_group(pending)
            counts_verified = accepted == duplicates == span_count == profile.events
        except (
            QualificationFailure,
            TimeoutError,
            KeyError,
            ValueError,
            TypeError,
            IndexError,
            ArithmeticError,
            StopIteration,
        ) as exc:
            fail(
                str(exc)
                if isinstance(exc, QualificationFailure)
                else "LOAD_INCOMPLETE_OR_INVALID_RECEIPT"
            )
        finally:
            # A lost create acknowledgement remains explicitly unresolved. Never
            # delete a project inferred from a partial name or an unrelated listing.
            for tenant in tenants:
                try:
                    await request("DELETE", tenant["base"], params={"confirm_slug": tenant["slug"]})
                    await request("GET", tenant["base"], expected=404)
                    cleanup.append(
                        {
                            "project_id": tenant["id"],
                            "deleted": True,
                            "project_absence_verified": True,
                        }
                    )
                except (QualificationFailure, KeyError, ValueError):
                    fail("LOAD_OWNED_PROJECT_CLEANUP_UNCONFIRMED")
                    cleanup.append({"project_id": tenant["id"], "deleted": False})
    metrics = {
        key: latency_summary(values, minimum=profile.minimum_latency_samples)
        for key, values in samples.items()
    }
    rate = accepted / (workload_ns / 1000000000) if workload_ns and counts_verified else None
    gates = {}
    for key, metric in metrics.items():
        ceiling = (
            profile.p95_query_ms_ceiling if key.endswith("query") else profile.p95_ingest_ms_ceiling
        )
        gates[key + "_p95"] = None if metric["p95_ms"] is None else metric["p95_ms"] <= ceiling
    gates["unique_events_per_mixed_ingest_second"] = (
        None if rate is None else rate >= profile.minimum_unique_events_per_second
    )
    cleanup_complete = (
        not unknown_creations and len(cleanup) == 2 and all(row["deleted"] for row in cleanup)
    )
    passed = (
        not errors
        and counts_verified
        and isolation_verified
        and cleanup_complete
        and all(value is True for value in gates.values())
    )
    value = {
        "contract": "cutback.local-load-receipt.v1",
        "receipt_id": str(uuid4()),
        "status": "PASSED_DECLARED_LOCAL_ENVELOPE"
        if passed
        else "FAILED_OR_INCOMPLETE_QUALIFICATION",
        "recorded_at": datetime.now(UTC).isoformat(),
        "profile": profile.model_dump(mode="json"),
        "profile_sha256": fingerprint(profile.model_dump(mode="json")),
        "catalog_sha256": Catalog().sha256,
        "scope": "owned_local_http_ingestion_trace_usage_query_and_viewer_tenant_isolation",
        "workload_phases": [
            "concurrent_event_replay_and_span_ingestion",
            "concurrent_post_ingestion_trace_and_usage_queries",
        ],
        "host": {
            "os": platform.system(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
        },
        "unique_events_acknowledged": accepted,
        "duplicate_replays_acknowledged": duplicates,
        "root_spans_acknowledged": span_count,
        "client_inflight_peak": inflight["peak"],
        "aggregate_counts_and_unknown_cost_verified": counts_verified,
        "viewer_cross_tenant_denial_verified": isolation_verified,
        "mixed_ingest_workload_ns": workload_ns,
        "unique_events_per_mixed_ingest_second": rate,
        "latency": metrics,
        "raw_client_request_latencies_ns": samples,
        "latency_attempts": "completed_failed_and_cancelled_attempts_errors_block_overall_pass",
        "declared_gates": gates,
        "errors": errors,
        "cleanup": cleanup,
        "unresolved_creation_slugs": unknown_creations,
        "cleanup_complete": cleanup_complete,
        "provider_calls": 0,
        "telemetry_origin": "synthetic_metadata_not_native_vendor_measurements",
        "customer_payload_capture": False,
        "production_capacity_claim": False,
        "worker_online_evaluation_and_gateway_capacity": "not_measured",
        "trust": "local_measurement_not_independent_attestation",
    }
    # The digest detects changed files; it does not turn local measurements into an attestation.
    return {
        **value,
        "receipt_sha256": hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
