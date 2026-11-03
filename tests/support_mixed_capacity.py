"""Owned synthetic service-capacity qualification, never a production load generator.

HTTP collector/gateway traffic and real PostgreSQL/native worker paths overlap.
Only provider transports are closed synthetic fixtures. No customer data or live
model request belongs in this harness.
"""

import json
import os
import platform
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from importlib.metadata import version
from typing import Literal
from uuid import UUID, uuid4

import httpx
from openai import OpenAI
from pydantic import Field, StrictInt
from sqlalchemy import func, select
from support_evaluation import authorized_dataset, plan_for
from support_gateway import issue_gateway_key, native_response, prepare_gateway
from support_http import owned_collector

from cutback.core.models import LLMEvent, StrictModel
from cutback.core.prompts import fingerprint
from cutback.evals.executors import OpenAIExecutor
from cutback.gateway.app import create_gateway
from cutback.gateway.client import operation_headers
from cutback.load_qualification import latency_summary
from cutback.server.db import ExperimentRunRecord, Project, StoredEvent
from cutback.server.execution_db import ExecutionCall
from cutback.server.gateway_db import GatewayCallRow
from cutback.server.monitoring import queue_online_intake
from cutback.server.quality_contracts import JUDGE_SCHEMA
from cutback.server.quality_db import QualityEvaluation, QualityRule
from cutback.server.quality_worker import run_quality_one
from cutback.server.worker import run_one

KINDS = ("ingestion_cycle", "query_cycle", "experiment_batch", "quality_cycle", "gateway_request")


class MixedProfile(StrictModel):
    contract: Literal["cutback.owned-mixed-capacity.v1"] = "cutback.owned-mixed-capacity.v1"
    duration_seconds: StrictInt = Field(default=30, ge=10, le=600)
    max_operations_per_actor: StrictInt = Field(default=5000, ge=2, le=5000)
    batch_size: StrictInt = Field(default=10, ge=2, le=20, multiple_of=2)
    interval_seconds: float = Field(default=0.2, strict=True, ge=0.05, le=2)
    minimum_samples: StrictInt = Field(default=1, ge=1, le=100)
    http_p95_ms_ceiling: float = Field(default=60000.0, strict=True, gt=0, le=120000)
    experiment_p95_ms_ceiling: float = Field(default=180000.0, strict=True, gt=0, le=300000)
    drain_seconds: StrictInt = Field(default=180, ge=30, le=300)


class MixedFailure(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise MixedFailure(code)


def response_json(response, expected=200):
    require(len(response.content) <= 2097152, "MIXED_HTTP_RESPONSE_BOUND_EXCEEDED")
    if response.status_code != expected:
        detail = response.json().get("detail")
        safe = (
            detail
            if isinstance(detail, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,100}", detail)
            else "DETAIL_OMITTED"
        )
        raise MixedFailure("MIXED_HTTP_" + str(response.status_code) + "_" + safe)
    return response.json()


class Measurements:
    def __init__(self, profile):
        self.profile, self.lock = profile, threading.Lock()
        self.samples = {kind: [] for kind in KINDS}
        self.active, self.peak_kinds, self.peak_requests = Counter(), 0, 0
        self.errors, self.progress = Counter(), Counter()
        self.actor_progress = Counter()
        self.stop = threading.Event()
        self.start_ns = self.end_ns = None

    @contextmanager
    def operation(self, kind):
        start, successful = time.perf_counter_ns(), False
        with self.lock:
            self.active[kind] += 1
            self.peak_kinds = max(self.peak_kinds, len(self.active))
            self.peak_requests = max(self.peak_requests, sum(self.active.values()))
        try:
            yield
            successful = True
        finally:
            end = time.perf_counter_ns()
            with self.lock:
                require(len(self.samples[kind]) < 10000, "MIXED_RAW_SAMPLE_BOUND_EXCEEDED")
                self.samples[kind].append(
                    {"start_ns": start, "end_ns": end, "successful": successful}
                )
                self.active[kind] -= 1
                if not self.active[kind]:
                    del self.active[kind]

    def error(self, error, kind="setup_or_verification"):
        code = str(error) if isinstance(error, MixedFailure) else "MIXED_OPERATION_FAILED"
        with self.lock:
            self.errors[kind + ":" + code] += 1
        self.stop.set()

    def increment(self, key, count=1):
        with self.lock:
            self.progress[key] += count

    def report(self):
        metrics = {}
        for kind in KINDS:
            raw = self.samples[kind]
            latencies = [row["end_ns"] - row["start_ns"] for row in raw]
            summary = latency_summary(latencies, minimum=self.profile.minimum_samples)
            ceiling = (
                self.profile.experiment_p95_ms_ceiling
                if kind == "experiment_batch"
                else self.profile.http_p95_ms_ceiling
            )
            metrics[kind] = {
                **summary,
                "raw_intervals": raw,
                "scope": "whole_actor_operation_wall_time_including_its_HTTP_or_worker_work",
                "completed_within_window": sum(
                    row["successful"] and row["end_ns"] <= self.end_ns for row in raw
                ),
                "completed_during_drain": sum(
                    row["successful"] and row["end_ns"] > self.end_ns for row in raw
                ),
                "p95_ceiling_ms": ceiling,
                "within_window_operations_per_second": sum(
                    row["successful"] and row["end_ns"] <= self.end_ns for row in raw
                )
                / self.profile.duration_seconds,
                "passed": summary["p95_ms"] is not None
                and summary["p95_ms"] <= ceiling
                and sum(row["successful"] and row["end_ns"] <= self.end_ns for row in raw)
                >= 2 * self.profile.minimum_samples
                and all(row["successful"] for row in raw),
            }
        return metrics


def native_fixture(model, text):
    return {
        "id": "resp_owned_capacity",
        "object": "response",
        "created_at": 1,
        "model": model,
        "status": "completed",
        "output": [
            {
                "id": "msg_owned_capacity",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": 20,
            "output_tokens": 10,
            "total_tokens": 30,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


def new_tenant(api, recorded):
    client, admin, _ = api
    slug = "synthetic-mixed-" + uuid4().hex
    # Record intent before creation. Cleanup resolves exactly this random slug
    # if the acknowledgement is lost; it never searches/deletes other tenants.
    tenant = {"id": None, "slug": slug}
    recorded.append(tenant)
    project = response_json(client.post("/v1/projects", headers=admin, json={"slug": slug}), 201)[
        "id"
    ]
    tenant["id"] = project
    keys = []
    for role in ("ingest", "viewer"):
        row = response_json(
            client.post(
                f"/v1/projects/{project}/keys",
                headers=admin,
                json={"role": role, "environment": "staging"},
            ),
            201,
        )
        keys.append({"Authorization": "Bearer " + row["token"]})
    tenant.update(
        {
            "id": project,
            "slug": slug,
            "tuple": (project, *keys),
            "ingest": keys[0],
            "viewer": keys[1],
        }
    )
    return tenant


def create_mixed_quality(api, setup, *, max_evaluations, max_requests, **unused):
    client, admin, _ = api
    base, policy, _ = setup
    prompt = response_json(
        client.post(
            base + "/prompts",
            headers=admin,
            json={
                "key": "owned-mixed-quality",
                "workflow": "synthetic-support",
            },
        ),
        201,
    )
    version_row = response_json(
        client.post(
            base + "/prompts/" + prompt["id"] + "/versions",
            headers=admin,
            json={
                "version_id": str(uuid4()),
                "expected_latest_ordinal": 0,
                "policy_id": policy,
                "environment": "staging",
                "provenance_ref": "owned-mixed-capacity",
                "document": {
                    "text": "Judge captured input {{input}} and output {{output}}. "
                    "Return an exact supporting quote.",
                    "variables": {"input": "string", "output": "string"},
                    "output_schema": JUDGE_SCHEMA,
                },
            },
        ),
        201,
    )
    now = datetime.now(UTC)
    return response_json(
        client.post(
            base + "/quality-rules",
            headers=admin,
            json={
                "version_id": str(uuid4()),
                "key": "owned-mixed-quality",
                "expected_revision": 0,
                "environment": "staging",
                "workflow": "synthetic-support",
                "source_policy_id": policy,
                "evaluator": "model_judge",
                "content_use_authorized": True,
                "starts_at": (now - timedelta(minutes=1)).isoformat(),
                "expires_at": (now + timedelta(hours=1)).isoformat(),
                "max_evaluations": max_evaluations,
                "max_requests": max_requests,
                "max_total_usd": "1",
                "approval_ref": "owned-mixed-capacity",
                "prompt_version_id": version_row["id"],
                "judge": {
                    "provider": "openai",
                    "model": "gpt-4.1-mini",
                    "surface": "responses.create",
                },
            },
        ),
        201,
    )


def setup_tenant(api, tenant, store, directory, monkeypatch, profile):
    client, admin, app = api
    identity = tenant["tuple"]
    base, _, dataset, _ = authorized_dataset(api, identity, directory, monkeypatch, store=store)
    spec = plan_for(
        dataset,
        baseline={
            "key": "baseline",
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "max_output_tokens": 64,
        },
        candidates=[
            {
                "key": "candidate",
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "max_output_tokens": 32,
            }
        ],
        approved_providers=["openai"],
    )
    policy = response_json(
        client.post(
            base + "/capture-policies",
            headers=admin,
            json={
                "enabled": True,
                "approval_ref": "owned-mixed-quality",
                "rights_basis": "synthetic",
                "environments": ["staging"],
                "workflows": ["synthetic-support"],
                "allowed_providers": ["local", "openai"],
                "max_samples": profile.max_operations_per_actor,
                "sample_rate": 1,
                "retention_days": 1,
                "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            },
        ),
        201,
    )
    quality = create_mixed_quality(
        api,
        (base, policy["id"], store),
        kind="model_judge",
        max_evaluations=profile.max_operations_per_actor,
        max_requests=profile.max_operations_per_actor,
        max_total_usd="1",
    )
    gateway = prepare_gateway(
        api,
        identity,
        requests=profile.max_operations_per_actor,
        concurrency=min(8, profile.max_operations_per_actor),
        cap="10",
    )
    key = issue_gateway_key(gateway)
    tenant.update(
        base=base,
        plan_specification={
            "dataset_id": dataset["id"],
            "specification": spec.model_dump(mode="json"),
        },
        policy=policy,
        quality=quality,
        gateway=gateway,
        gateway_key=key,
        events=0,
        batches=0,
        runs=[],
    )


def run_mixed(api, directory, monkeypatch, profile, *, mode="success"):
    """Dev acceptance only: generated tenants and local services, no provider network."""
    from pydantic import SecretStr

    from cutback.evals.artifacts import EncryptedFileStore

    profile = MixedProfile.model_validate(profile)
    client, admin, app = api
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "owned-synthetic-capacity-key")
    store = EncryptedFileStore(directory / "encrypted", os.urandom(32))
    app.state.artifact_store = store
    measurements, tenants, cleanup = Measurements(profile), [], []
    native_counts, native_lock = Counter(), threading.Lock()
    canonical_accounting = {}
    native_accounting = {}

    def execute_transport(kind):
        def handle(request):
            body = json.loads(request.content)
            require(
                str(request.url) == "https://api.openai.com/v1/responses",
                "MIXED_UNEXPECTED_PROVIDER_PATH",
            )
            with native_lock:
                native_counts[kind] += 1
            time.sleep(0.005)
            text = (
                json.dumps({"outcome": "passed", "evidence_quote": "urgent"})
                if kind == "quality"
                else "A"
            )
            return httpx.Response(200, json=native_fixture(body["model"], text))

        return httpx.MockTransport(handle)

    async def gateway_transport(request):
        with native_lock:
            native_counts["gateway"] += 1
        if mode == "gateway_outage":
            return httpx.Response(429, json={"error": {"message": "owned synthetic outage"}})
        return httpx.Response(200, json=native_response())

    try:
        for index in range(2):
            tenant = new_tenant(api, tenants)
            setup_tenant(api, tenant, store, directory / str(index), monkeypatch, profile)
        gateway = create_gateway(
            database_url=app.state.engine.url.render_as_string(hide_password=False),
            enabled=True,
            provider_key=SecretStr("owned-synthetic-capacity-upstream"),
            hmac_key=SecretStr("owned-synthetic-capacity-hmac-" + "x" * 32),
            transport=httpx.MockTransport(gateway_transport),
            allow_insecure_http=True,
            allowed_project_ids=frozenset(UUID(tenant["id"]) for tenant in tenants),
        )
        with owned_collector(app) as endpoint, owned_collector(gateway) as gateway_endpoint:
            barrier = threading.Barrier(2 * len(KINDS) + 1)

            def actor(tenant, kind):
                executor = None
                native = None
                try:
                    http = httpx.Client(
                        base_url=endpoint, timeout=30, trust_env=False, follow_redirects=False
                    )
                    if kind in {"experiment_batch", "quality_cycle"}:
                        executor = OpenAIExecutor(
                            http_client=httpx.Client(
                                transport=execute_transport(
                                    "worker" if kind == "experiment_batch" else "quality"
                                ),
                                trust_env=False,
                            )
                        )
                    if kind == "gateway_request":
                        native = OpenAI(
                            api_key=tenant["gateway_key"]["token"],
                            base_url=gateway_endpoint + "/v1",
                            max_retries=0,
                            http_client=httpx.Client(trust_env=False, timeout=30),
                        )
                    barrier.wait(timeout=30)
                    for _ in range(profile.max_operations_per_actor):
                        if (
                            measurements.stop.is_set()
                            or time.perf_counter_ns() >= measurements.end_ns
                        ):
                            break
                        with measurements.operation(kind):
                            exercise(kind, tenant, http, native, executor)
                        with measurements.lock:
                            if time.perf_counter_ns() <= measurements.end_ns:
                                measurements.actor_progress[tenant["id"] + ":" + kind] += 1
                        measurements.stop.wait(profile.interval_seconds)
                    else:
                        require(
                            time.perf_counter_ns() >= measurements.end_ns
                            or measurements.stop.is_set(),
                            "MIXED_ACTOR_OPERATION_BOUND_EXHAUSTED_BEFORE_WINDOW_END",
                        )
                except BaseException as error:
                    measurements.error(error, kind)
                finally:
                    if executor:
                        executor.close()
                    if native:
                        native.close()
                    if "http" in locals():
                        http.close()

            def exercise(kind, tenant, http, native, executor):
                base = tenant["base"]
                if kind == "ingestion_cycle":
                    events, spans = [], []
                    for index in range(profile.batch_size):
                        trace, root_span = uuid4().hex, uuid4().hex[:16]
                        now = datetime.now(UTC)
                        event = LLMEvent(
                            environment="staging",
                            service_name="owned-mixed-metadata",
                            provider="openai",
                            sdk_surface="responses.create",
                            trace_id=trace,
                            span_id=uuid4().hex[:16],
                            parent_span_id=root_span,
                            requested_model="gpt-4.1-mini" if index % 2 == 0 else "owned-unpriced",
                            duration_ms=1,
                            status="success",
                            input_tokens=20,
                            output_tokens=10,
                            cache_read_tokens=0,
                            reasoning_tokens=0,
                            usage_source="provider_reported",
                            metering_status="text_only",
                        )
                        events.append(event.model_dump(mode="json"))
                        ns = int(now.timestamp()) * 1000000000 + now.microsecond * 1000
                        spans.append(
                            {
                                "trace_id": trace,
                                "span_id": root_span,
                                "operation": "mixed-metadata",
                                "kind": "chain",
                                "service": "owned-mixed-metadata",
                                "workflow": "mixed-metadata",
                                "started_ns": ns,
                                "ended_ns": ns + 1000,
                                "status": "unknown",
                            }
                        )
                    body = {"project_id": tenant["id"], "events": events}
                    first = response_json(
                        http.post("/v1/ingest/events", headers=tenant["ingest"], json=body)
                    )
                    repeated = response_json(
                        http.post("/v1/ingest/events", headers=tenant["ingest"], json=body)
                    )
                    traced = response_json(
                        http.post(
                            "/v1/ingest/spans", headers=tenant["ingest"], json={"spans": spans}
                        )
                    )
                    require(
                        first["accepted"]
                        == repeated["duplicate"]
                        == traced["accepted"]
                        == profile.batch_size
                        and repeated["accepted"] == first["duplicate"] == 0,
                        "MIXED_ACK_OR_DUPLICATE_COUNT_MISMATCH",
                    )
                    tenant["events"] += profile.batch_size
                    tenant["batches"] += 1
                elif kind == "query_cycle":
                    response_json(
                        http.get(
                            base + "/traces",
                            headers=tenant["viewer"],
                            params={
                                "environment_id": str(tenant["gateway"]["environment"]),
                                "limit": 10,
                            },
                        )
                    )
                    usage = response_json(http.get(base + "/usage", headers=tenant["viewer"]))[
                        "summary"
                    ]
                    if usage["unpriced_requests"]:
                        require(
                            usage["modeled_cost_usd"] is None, "MIXED_UNKNOWN_FULL_COST_WAS_LOST"
                        )
                    require(
                        "owned synthetic urgent" not in json.dumps(usage),
                        "MIXED_QUERY_CONTENT_LEAK",
                    )
                elif kind == "experiment_batch":
                    plan = response_json(
                        http.post(
                            base + "/experiments/plans",
                            headers=admin,
                            json=tenant["plan_specification"],
                        ),
                        201,
                    )
                    response_json(
                        http.post(
                            base + "/experiments/plans/" + plan["id"] + "/approve",
                            headers=admin,
                            json={
                                "plan_hash": plan["plan_hash"],
                                "approval_ref": "owned-mixed-capacity",
                            },
                        )
                    )
                    queued = response_json(
                        http.post(
                            base + "/experiments",
                            headers=admin,
                            json={"plan_id": plan["id"]},
                        ),
                        202,
                    )
                    tenant["runs"].append(UUID(queued["id"]))
                    result = run_one(
                        app.state.sessions,
                        store,
                        executor=executor,
                        execution_enabled=True,
                        only_run_id=queued["id"],
                    )
                    require(result["status"] == "COMPLETED", "MIXED_EXPERIMENT_NOT_COMPLETED")
                    measurements.increment("completed_experiments")
                elif kind == "quality_cycle":
                    trace, span = uuid4().hex, uuid4().hex[:16]
                    ns = time.time_ns()
                    response_json(
                        http.post(
                            "/v1/ingest/spans",
                            headers=tenant["ingest"],
                            json={
                                "spans": [
                                    {
                                        "trace_id": trace,
                                        "span_id": span,
                                        "operation": "mixed-quality",
                                        "service": "synthetic-desk",
                                        "workflow": "synthetic-support",
                                        "kind": "chain",
                                        "status": "unknown",
                                        "started_ns": ns,
                                        "ended_ns": ns + 1000,
                                    }
                                ]
                            },
                        )
                    )
                    response_json(
                        http.post(
                            "/v1/ingest/trace-content",
                            headers=tenant["ingest"],
                            json={
                                "policy_id": tenant["policy"]["id"],
                                "sample_id": str(uuid4()),
                                "trace_id": trace,
                                "span_id": span,
                                "provider": "local",
                                "input": "owned synthetic urgent input",
                                "output": "urgent",
                                "provenance_ref": "owned-mixed-capacity",
                            },
                        ),
                        201,
                    )
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        queue_online_intake(
                            app.state.sessions, enabled=True, only_project_id=UUID(tenant["id"])
                        )
                        with app.state.sessions() as session:
                            own = session.scalar(
                                select(QualityEvaluation).where(
                                    QualityEvaluation.rule_id == UUID(tenant["quality"]["id"]),
                                    QualityEvaluation.trace_id == trace,
                                    QualityEvaluation.span_id == span,
                                )
                            )
                            state = own.state if own else None
                        if state == "passed":
                            break
                        require(
                            state not in {"failed", "error", "unknown"},
                            "MIXED_QUALITY_TERMINAL_FAILURE",
                        )
                        result = run_quality_one(
                            app.state.sessions, store, executor=executor, execution_enabled=True
                        )
                        require(
                            result["status"] in {"IDLE", "passed"},
                            "MIXED_QUALITY_NATIVE_OUTCOME_FAILED",
                        )
                        # Idle intake/worker polls are not billable retries. A
                        # different owned worker may already be settling this
                        # target; only its canonical exact row proves completion.
                        time.sleep(0.01)
                    else:
                        raise MixedFailure("MIXED_QUALITY_TARGET_DID_NOT_SETTLE")
                    measurements.increment("completed_quality")
                else:
                    result = native.responses.create(
                        model="gpt-6-luna",
                        input="owned synthetic gateway input",
                        max_output_tokens=128,
                        store=False,
                        service_tier="default",
                        reasoning={"effort": "medium"},
                        extra_headers=operation_headers(uuid4()),
                    )
                    require(
                        result.status == "completed" and result.output_text == "A",
                        "MIXED_NATIVE_GATEWAY_RESPONSE_CHANGED",
                    )
                    measurements.increment("completed_gateway")

            pool = ThreadPoolExecutor(max_workers=2 * len(KINDS))
            futures = [pool.submit(actor, tenant, kind) for tenant in tenants for kind in KINDS]
            try:
                measurements.start_ns = time.perf_counter_ns()
                measurements.end_ns = measurements.start_ns + profile.duration_seconds * 1000000000
                barrier.wait(timeout=30)
                measurements.stop.wait(profile.duration_seconds)
                measurements.stop.set()
                _, pending = wait(futures, timeout=profile.drain_seconds)
                require(not pending, "MIXED_ACTORS_DID_NOT_DRAIN")
            finally:
                measurements.stop.set()
                pool.shutdown(wait=True, cancel_futures=True)
            for index, tenant in enumerate(tenants):
                other = tenants[1 - index]
                denied = client.get(other["base"] + "/usage", headers=tenant["viewer"])
                require(denied.status_code in (403, 404), "MIXED_CROSS_TENANT_READ_NOT_DENIED")
            with app.state.sessions() as session:
                ids = [UUID(tenant["id"]) for tenant in tenants]
                unsettled = session.scalar(
                    select(func.count())
                    .select_from(ExecutionCall)
                    .where(ExecutionCall.project_id.in_(ids), ExecutionCall.spent_usd.is_(None))
                )
                gateway_unknown = session.scalar(
                    select(func.count())
                    .select_from(GatewayCallRow)
                    .where(GatewayCallRow.project_id.in_(ids), GatewayCallRow.state != "SETTLED")
                )
                quality_unknown = session.scalar(
                    select(func.count())
                    .select_from(QualityEvaluation)
                    .where(
                        QualityEvaluation.project_id.in_(ids),
                        QualityEvaluation.state.notin_(["passed", "failed"]),
                    )
                )
                for tenant in tenants:
                    count = session.scalar(
                        select(func.count())
                        .select_from(StoredEvent)
                        .where(
                            StoredEvent.project_id == UUID(tenant["id"]),
                            StoredEvent.event["service_name"].as_string() == "owned-mixed-metadata",
                        )
                    )
                    require(count == tenant["events"], "MIXED_CANONICAL_METADATA_COUNT_MISMATCH")
                    priced, known = session.execute(
                        select(
                            func.count(StoredEvent.cost_usd), func.sum(StoredEvent.cost_usd)
                        ).where(
                            StoredEvent.project_id == UUID(tenant["id"]),
                            StoredEvent.event["service_name"].as_string() == "owned-mixed-metadata",
                        )
                    ).one()
                    require(
                        count > 0
                        and priced * 2 == count
                        and isinstance(known, Decimal)
                        and known > 0,
                        "MIXED_KNOWN_AND_UNKNOWN_COST_COVERAGE_CHANGED",
                    )
                    canonical_accounting[tenant["id"]] = {
                        "unique_metadata_events": count,
                        "priced_metadata_events": priced,
                        "unpriced_metadata_events": count - priced,
                        "metadata_known_subtotal_usd": str(known),
                        "metadata_full_cost_usd": None,
                    }
                require(
                    unsettled == gateway_unknown == quality_unknown == 0,
                    "MIXED_NATIVE_ACCOUNTING_INCOMPLETE",
                )
                rows = list(
                    session.scalars(
                        select(ExperimentRunRecord).where(ExperimentRunRecord.project_id.in_(ids))
                    )
                )
                require(
                    len(rows) == measurements.progress["completed_experiments"]
                    and all(
                        row.status == "COMPLETED"
                        and row.call_ledger_version == 1
                        and row.requests_used == row.known_cost_requests
                        and row.reserved_usd >= row.spent_usd
                        and row.spent_usd > 0
                        for row in rows
                    ),
                    "MIXED_EXPERIMENT_CANONICAL_TOTALS_INCOMPLETE",
                )
                worker_requests = sum(row.requests_used for row in rows)
                require(
                    worker_requests == native_counts["worker"],
                    "MIXED_NATIVE_WORKER_ATTEMPT_COUNT_MISMATCH",
                )
                call_count, call_spend = session.execute(
                    select(func.count(ExecutionCall.id), func.sum(ExecutionCall.spent_usd)).where(
                        ExecutionCall.project_id.in_(ids)
                    )
                ).one()
                require(
                    call_count == worker_requests
                    and call_spend == sum((row.spent_usd for row in rows), Decimal(0)),
                    "MIXED_DURABLE_CALL_LEDGER_TOTAL_MISMATCH",
                )
                rules = list(
                    session.scalars(select(QualityRule).where(QualityRule.project_id.in_(ids)))
                )
                quality_requests = sum(row.requests_used for row in rules)
                require(
                    quality_requests
                    == native_counts["quality"]
                    == measurements.progress["completed_quality"]
                    and all(
                        row.requests_used == row.known_cost_requests
                        and row.reserved_usd >= row.spent_usd
                        and row.spent_usd > 0
                        for row in rules
                    ),
                    "MIXED_NATIVE_QUALITY_ATTEMPT_COUNT_MISMATCH",
                )
                quality_count, quality_spend = session.execute(
                    select(
                        func.count(QualityEvaluation.id), func.sum(QualityEvaluation.spent_usd)
                    ).where(QualityEvaluation.project_id.in_(ids))
                ).one()
                require(
                    quality_count == quality_requests
                    and quality_spend == sum((row.spent_usd for row in rules), Decimal(0)),
                    "MIXED_DURABLE_QUALITY_LEDGER_TOTAL_MISMATCH",
                )
                gateway_rows = list(
                    session.scalars(
                        select(GatewayCallRow).where(GatewayCallRow.project_id.in_(ids))
                    )
                )
                require(
                    len(gateway_rows)
                    == native_counts["gateway"]
                    == measurements.progress["completed_gateway"]
                    and all(row.cost_usd is not None and row.cost_usd > 0 for row in gateway_rows),
                    "MIXED_NATIVE_GATEWAY_ATTEMPT_COUNT_MISMATCH",
                )
                native_accounting = {
                    "worker": {
                        "requests": worker_requests,
                        "known_subtotal_usd": str(sum((row.spent_usd for row in rows), Decimal(0))),
                    },
                    "quality": {
                        "requests": quality_requests,
                        "known_subtotal_usd": str(
                            sum((row.spent_usd for row in rules), Decimal(0))
                        ),
                    },
                    "gateway": {
                        "requests": len(gateway_rows),
                        "known_subtotal_usd": str(
                            sum((row.cost_usd for row in gateway_rows), Decimal(0))
                        ),
                    },
                    "cost_source": "synthetic_provider_usage_plus_pinned_model_prices",
                    "invoice_verified": False,
                }
    except BaseException as error:
        measurements.error(error)
    finally:
        for tenant in reversed(tenants):
            removed = False
            try:
                if tenant["id"] is None:
                    with app.state.sessions() as session:
                        rows = list(
                            session.scalars(
                                select(Project.id).where(Project.slug == tenant["slug"])
                            )
                        )
                    require(len(rows) <= 1, "MIXED_OWNED_CLEANUP_IDENTITY_AMBIGUOUS")
                    tenant["id"] = str(rows[0]) if rows else None
                if tenant["id"] is None:
                    removed = True
                else:
                    response = client.delete(
                        "/v1/projects/" + tenant["id"],
                        headers=admin,
                        params={"confirm_slug": tenant["slug"]},
                    )
                    with app.state.sessions() as session:
                        removed = (
                            response.status_code == 200
                            and session.get(Project, UUID(tenant["id"])) is None
                        )
            except BaseException:
                measurements.errors["MIXED_OWNED_CLEANUP_FAILED"] += 1
            cleanup.append({"project_id": tenant["id"], "removed": removed})
            if not removed:
                measurements.errors["MIXED_OWNED_CLEANUP_INCOMPLETE"] += 1
    metrics = measurements.report() if measurements.start_ns else {}
    for tenant in tenants:
        for kind in KINDS:
            if (
                measurements.actor_progress[str(tenant["id"]) + ":" + kind]
                < profile.minimum_samples
            ):
                measurements.errors[kind + ":MIXED_ACTOR_WITHIN_WINDOW_SAMPLE_MINIMUM_NOT_MET"] += 1
    passed = (
        bool(metrics)
        and not measurements.errors
        and all(row["passed"] for row in metrics.values())
        and measurements.peak_kinds >= 3
        and len(tenants) == 2
        and all(
            measurements.actor_progress[tenant["id"] + ":" + kind] >= profile.minimum_samples
            for tenant in tenants
            for kind in KINDS
        )
        and len(cleanup) == 2
        and all(row["removed"] for row in cleanup)
    )
    receipt = {
        "contract": "cutback.owned-mixed-capacity-receipt.v1",
        "profile": profile.model_dump(mode="json"),
        "profile_hash": fingerprint(profile.model_dump(mode="json")),
        "status": "PASSED_DECLARED_SYNTHETIC_MIXED_ENVELOPE"
        if passed
        else "FAILED_OR_INCOMPLETE_MIXED_QUALIFICATION",
        "tenants": len(tenants),
        "actor_kinds": list(KINDS),
        "actors": 2 * len(KINDS),
        "metrics": metrics,
        "client_peak_concurrent_kinds": measurements.peak_kinds,
        "client_peak_concurrent_actor_operations": measurements.peak_requests,
        "progress": dict(measurements.progress),
        "actor_within_window_completions": dict(measurements.actor_progress),
        "canonical_metadata_accounting": canonical_accounting,
        "canonical_native_accounting": native_accounting,
        "errors": dict(measurements.errors),
        "native_sdk_fixture_calls": dict(native_counts),
        "openai_sdk": version("openai"),
        "cleanup": cleanup,
        "cleanup_complete": len(cleanup) == 2 and all(row["removed"] for row in cleanup),
        "provider_transport": "closed synthetic native HTTPX; real gateway-front HTTP",
        "collector_transport": "real owned loopback HTTP and PostgreSQL",
        "sample_semantics": "all starts retained; within-window completions exclude drain",
        "within_window_unique_metadata_events_per_second": (
            metrics["ingestion_cycle"]["completed_within_window"]
            * profile.batch_size
            / profile.duration_seconds
        )
        if metrics
        else None,
        "resources_and_competing_load": "operator-controlled, not independently attested",
        "runtime": {
            "python": platform.python_version(),
            "system": platform.system(),
            "machine": platform.machine(),
            "logical_cpu_count": os.cpu_count(),
        },
        "paid_provider_calls": 0,
        "production_sla": False,
        "customer_quality_verified": False,
        "invoice_verified_cost_usd": None,
    }
    return {**receipt, "receipt_hash": fingerprint(receipt)}
