"""Explicit metadata-only policy activity; never inspect inputs, responses or cache keys."""

import queue
import threading
import time
from collections import Counter
from uuid import UUID, uuid4

import httpx

from cutback.core.config import validate_endpoint
from cutback.core.health import HealthBatch
from cutback.core.policy_activity import PolicyRuntimeBatch, PolicyRuntimeEvent


class PolicyActivityObserver:
    def __init__(
        self,
        *,
        service_name,
        environment,
        endpoint,
        project_id,
        token,
        attribution,
        capacity=512,
        timeout_seconds=0.3,
        batch_size=50,
    ):
        endpoint = validate_endpoint(endpoint)
        if not endpoint.endswith("/v1/ingest/events") or attribution is None:
            raise ValueError("POLICY_ACTIVITY_EVENT_ENDPOINT_AND_CURRENT_SOURCE_MAP_REQUIRED")
        if (
            not token
            or len(token) < 32
            or not 1 <= capacity <= 10000
            or not 1 <= batch_size <= 200
            or not 0.01 <= timeout_seconds <= 2
        ):
            raise ValueError("POLICY_ACTIVITY_SCOPED_KEY_AND_BOUNDED_EXPORT_REQUIRED")
        self.service_name, self.environment = service_name, environment
        self.endpoint = endpoint.removesuffix("events") + "policy-runtime"
        self.project_id, self._token = UUID(project_id), token
        if not self.project_id.int:
            raise ValueError("POLICY_ACTIVITY_NONZERO_PROJECT_REQUIRED")
        self.attribution = attribution
        self._timeout, self._batch_size = timeout_seconds, batch_size
        self._queue: queue.Queue[PolicyRuntimeEvent] = queue.Queue(capacity)
        self._lock = threading.Lock()
        self._counters: Counter = Counter()
        self.closed = False
        self.instance_id = uuid4()
        self._last_health = float("-inf")
        self._thread = threading.Thread(
            target=self._run, name="cutback-policy-activity", daemon=True
        )
        self._thread.start()

    def count(self, key, amount=1):
        with self._lock:
            self._counters["policy_runtime_" + key] += amount

    def begin(self, *, workflow, **metadata):
        source = self.attribution.resolve()
        if not source.get("source_commit") or not source.get("call_site_id"):
            self.count("unattributed")
            return None
        return dict(
            metadata,
            workflow=workflow,
            call_site_id=source["call_site_id"],
            source_commit=source["source_commit"],
            service_name=self.service_name,
            environment=self.environment,
            event_id=uuid4(),
            started_ns=time.time_ns(),
        )

    def finish(self, token, *, completion, receipt=None):
        if token is None:
            return
        requested = token["cache_requested"]
        fields = dict(
            state="inactive",
            native_sdk_invocations=1,
            cache_operations=0,
            cache_write="not_applicable",
            usage_origin="native_response" if completion == "completed" else None,
        )
        if requested:
            fields = dict(
                state="unknown",
                native_sdk_invocations=None,
                cache_operations=None,
                cache_write=None,
                usage_origin=None,
            )
            if completion == "completed" and receipt is not None:
                hit = receipt.origin == "encrypted_cache"
                fields = dict(
                    state="hit" if hit else "native_miss",
                    native_sdk_invocations=receipt.native_sdk_calls,
                    cache_operations=1 if hit or receipt.cache_write == "not_eligible" else 2,
                    cache_write=receipt.cache_write,
                    usage_origin=receipt.usage_origin,
                )
        event = PolicyRuntimeEvent.model_validate(
            {**token, "ended_ns": time.time_ns(), "completion": completion, **fields}
        )
        with self._lock:
            self._counters["policy_runtime_observed"] += 1
            if not self.closed:
                try:
                    self._queue.put_nowait(event)
                    self._counters["policy_runtime_queued"] += 1
                    return
                except queue.Full:
                    pass
            self._counters["policy_runtime_dropped"] += 1

    def health(self):
        with self._lock:
            return {
                **{
                    "policy_runtime_" + key: 0
                    for key in (
                        "observed",
                        "queued",
                        "dropped",
                        "exported",
                        "rejected",
                        "export_failed",
                        "unattributed",
                        "normalization_failed",
                    )
                },
                **dict(self._counters),
                "policy_runtime_queue_depth": self._queue.qsize(),
            }

    def flush(self, timeout_seconds=0.3):
        deadline = time.monotonic() + min(max(timeout_seconds, 0), 5)
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.002)
        return self._queue.unfinished_tasks == 0

    def close(self, timeout_seconds=0.3):
        with self._lock:
            self.closed = True
        return self.flush(timeout_seconds)

    def _run(self):
        # Zero retries, redirects and environment proxies. This worker cannot dispatch inference.
        with httpx.Client(timeout=self._timeout, follow_redirects=False, trust_env=False) as client:
            while not self.closed or not self._queue.empty():
                try:
                    batch = [self._queue.get(timeout=0.05)]
                except queue.Empty:
                    continue
                while len(batch) < self._batch_size:
                    try:
                        batch.append(self._queue.get_nowait())
                    except queue.Empty:
                        break
                try:
                    body = PolicyRuntimeBatch(
                        project_id=self.project_id, events=batch
                    ).model_dump_json()
                    if len(body.encode()) > 1_048_576:
                        self.count("dropped", len(batch))
                        continue
                    response = client.post(
                        self.endpoint,
                        content=body,
                        headers={
                            "Authorization": "Bearer " + self._token,
                            "Content-Type": "application/json",
                        },
                    )
                    receipt = response.json() if response.status_code == 200 else None
                    if (
                        isinstance(receipt, dict)
                        and type(receipt.get("accepted")) is int
                        and type(receipt.get("duplicate")) is int
                        and receipt["accepted"] >= 0
                        and receipt["duplicate"] >= 0
                        and receipt["accepted"] + receipt["duplicate"] == len(batch)
                    ):
                        self.count("exported", len(batch))
                    else:
                        self.count(
                            "rejected"
                            if response.status_code in {400, 401, 403, 409, 422, 429}
                            else "export_failed",
                            len(batch),
                        )
                        self.count("dropped", len(batch))
                except Exception:
                    self.count("export_failed", len(batch))
                    self.count("dropped", len(batch))
                finally:
                    if self._queue.empty() or time.monotonic() - self._last_health >= 60:
                        self._last_health = time.monotonic()
                        self._send_health(client)
                    for _ in batch:
                        self._queue.task_done()

    def _send_health(self, client):
        try:
            body = HealthBatch(
                project_id=self.project_id,
                instance_id=self.instance_id,
                environment=self.environment,
                service_name=self.service_name,
                counters=self.health(),
            )
            client.post(
                self.endpoint.removesuffix("policy-runtime") + "health",
                content=body.model_dump_json(),
                headers={
                    "Authorization": "Bearer " + self._token,
                    "Content-Type": "application/json",
                },
            )
        except Exception:
            pass
