"""Explicit business-outcome export. No success inference, disk spool or paid requests."""

import math
import queue
import threading
import time
from collections import Counter
from uuid import UUID

import httpx

from cutback.core.config import validate_endpoint
from cutback.core.task_outcomes import TaskOutcomeBatch, TaskOutcomeInput


class TaskOutcomeExporter:
    """Bounded best-effort metadata export using an environment-scoped ingest key.

    Producers supply the actual trace ID and explicit application result. A false
    return/health drop means evidence was not delivered; never assume missing
    results were successful. This class neither installs SDK hooks nor runs tools.
    """

    def __init__(
        self, *, endpoint, token, capacity=512, batch_size=50, timeout_seconds=0.3, transport=None
    ):
        self.endpoint = validate_endpoint(endpoint)
        if not isinstance(token, str) or len(token) < 32:
            raise ValueError("SCOPED_TASK_INGEST_TOKEN_REQUIRED")
        if type(capacity) is not int or not 1 <= capacity <= 10000:
            raise ValueError("TASK_EXPORT_QUEUE_BOUND")
        if type(batch_size) is not int or not 1 <= batch_size <= 100:
            raise ValueError("TASK_EXPORT_BATCH_BOUND")
        if (
            type(timeout_seconds) not in {int, float}
            or not math.isfinite(timeout_seconds)
            or not 0.01 <= timeout_seconds <= 2
        ):
            raise ValueError("TASK_EXPORT_TIMEOUT_BOUND")
        self._token, self._timeout, self._transport = token, timeout_seconds, transport
        self._queue: queue.Queue[TaskOutcomeInput] = queue.Queue(capacity)
        self._batch_size = batch_size
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._counters: Counter = Counter()
        self._thread = threading.Thread(target=self._run, name="cutback-task-outcomes", daemon=True)
        self._thread.start()

    def _count(self, key, count=1):
        with self._lock:
            self._counters[key] += count

    def emit(self, outcome: TaskOutcomeInput) -> bool:
        """Validate and enqueue immediately. Exceptions never reach application work."""
        self._count("observed")
        try:
            # Copy/revalidate mutable caller models and enforce freshness now, too.
            item = TaskOutcomeInput.model_validate(outcome.model_dump(mode="json"))
            TaskOutcomeBatch(items=[item])
            with self._lock:
                if self._closed.is_set():
                    self._counters["dropped"] += 1
                    return False
                self._queue.put_nowait(item)
                self._counters["queued"] += 1
            return True
        except queue.Full:
            self._count("dropped")
        except (ValueError, TypeError, AttributeError):
            self._count("invalid")
            self._count("dropped")
        return False

    def health(self):
        with self._lock:
            return {
                **{
                    key: 0
                    for key in (
                        "observed",
                        "queued",
                        "exported",
                        "dropped",
                        "invalid",
                        "export_failed",
                    )
                },
                **self._counters,
                "queue_depth": self._queue.qsize(),
                "delivery": "best_effort_no_disk_spool_no_automatic_retry",
            }

    def flush(self, timeout_seconds=0.3):
        if type(timeout_seconds) not in {float, int} or not math.isfinite(timeout_seconds):
            return False
        deadline = time.monotonic() + min(max(timeout_seconds, 0), 5)
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.002)
        return self._queue.unfinished_tasks == 0

    def close(self, timeout_seconds=0.3):
        with self._lock:
            self._closed.set()
        return self.flush(timeout_seconds)

    def _run(self):
        with httpx.Client(
            timeout=self._timeout,
            follow_redirects=False,
            trust_env=False,
            transport=self._transport,
        ) as client:
            while not self._closed.is_set() or not self._queue.empty():
                try:
                    first = self._queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                batch = [first]
                while len(batch) < self._batch_size:
                    try:
                        batch.append(self._queue.get_nowait())
                    except queue.Empty:
                        break
                try:
                    unique: dict[UUID, TaskOutcomeInput] = {}
                    for item in batch:
                        prior = unique.get(item.result_id)
                        if prior is not None and prior != item:
                            raise ValueError("CONFLICTING_TASK_IDENTITIES_IN_EXPORT_BATCH")
                        unique[item.result_id] = item
                    self._count("coalesced", len(batch) - len(unique))
                    body = TaskOutcomeBatch(items=list(unique.values())).model_dump_json()
                    if len(body.encode()) > 524288:
                        raise ValueError("TASK_EXPORT_BODY_BOUND")
                    response = client.post(
                        self.endpoint,
                        content=body,
                        headers={
                            "Authorization": "Bearer " + self._token,
                            "Content-Type": "application/json",
                        },
                    )
                    receipt = response.json() if response.status_code == 200 else None
                    valid = (
                        isinstance(receipt, dict)
                        and type(receipt.get("accepted")) is int
                        and type(receipt.get("duplicates")) is int
                        and receipt["accepted"] >= 0
                        and receipt["duplicates"] >= 0
                        and receipt["accepted"] + receipt["duplicates"] == len(unique)
                    )
                    if valid:
                        self._count("exported", len(batch))
                    else:
                        self._count("export_failed", len(batch))
                        self._count("dropped", len(batch))
                except (httpx.HTTPError, ValueError, TypeError):
                    self._count("export_failed", len(batch))
                    self._count("dropped", len(batch))
                finally:
                    for _ in batch:
                        self._queue.task_done()
