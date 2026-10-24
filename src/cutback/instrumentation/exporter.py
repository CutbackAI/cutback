"""Bounded, background-only export. The inference path never performs network I/O."""

import json
import queue
import threading
import time
from collections import Counter
from uuid import UUID

import httpx

from cutback.core.config import validate_endpoint
from cutback.core.models import EventBatch, LLMEvent


class Exporter:
    def __init__(
        self,
        *,
        endpoint: str,
        project_id: str,
        token: str,
        capacity: int = 512,
        timeout_seconds: float = 0.3,
        batch_size: int = 50,
    ):
        self.endpoint = validate_endpoint(endpoint)
        self.project_id = UUID(project_id)
        if not token or len(token) < 32:
            raise ValueError("a scoped token of at least 32 characters is required")
        if not 1 <= capacity <= 10000 or not 1 <= batch_size <= 200:
            raise ValueError("invalid queue or batch limit")
        if not 0.01 <= timeout_seconds <= 2:
            raise ValueError("export timeout must be 0.01–2 seconds")
        self._token = token
        self._timeout = timeout_seconds
        self._batch_size = batch_size
        self._queue: queue.Queue[LLMEvent] = queue.Queue(capacity)
        self._lock = threading.Lock()
        self._counters: Counter = Counter()
        self._closed = threading.Event()
        self._last_export: float | None = None
        self._thread = threading.Thread(target=self._run, name="cutback-export", daemon=True)
        self._thread.start()

    def count(self, key, amount=1):
        with self._lock:
            self._counters[key] += amount

    def emit(self, event: LLMEvent):
        self.count("observed")
        if self._closed.is_set():
            self.count("dropped")
            return
        try:
            self._queue.put_nowait(event)
            self.count("queued")
        except queue.Full:
            self.count("dropped")

    def health(self):
        with self._lock:
            return {
                **dict(self._counters),
                "queue_depth": self._queue.qsize(),
                "last_export_unix": self._last_export,
                "delivery": "best_effort_no_disk_spool",
            }

    def flush(self, timeout_seconds=0.3) -> bool:
        deadline = time.monotonic() + min(max(timeout_seconds, 0), 5)
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.002)
        return self._queue.unfinished_tasks == 0

    def close(self, timeout_seconds=0.3):
        self._closed.set()
        return self.flush(timeout_seconds)

    def _run(self):
        # No redirect following: never forward the project key to a redirected destination.
        with httpx.Client(timeout=self._timeout, follow_redirects=False, trust_env=False) as client:
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
                    body = EventBatch(project_id=self.project_id, events=batch).model_dump_json()
                    if len(body.encode()) > 1_048_576:
                        self.count("dropped", len(batch))
                        continue
                    # Zero retries is intentional: bounded loss rather than retry amplification.
                    response = client.post(
                        self.endpoint,
                        content=body,
                        headers={
                            "Authorization": f"Bearer {self._token}",
                            "Content-Type": "application/json",
                        },
                    )
                    receipt = response.json() if response.status_code == 200 else None
                    if receipt and receipt.get("accepted", 0) + receipt.get("duplicate", 0) == len(
                        batch
                    ):
                        self.count("exported", len(batch))
                        with self._lock:
                            self._last_export = time.time()
                    else:
                        self.count(
                            "rejected"
                            if response.status_code in {400, 401, 403, 422}
                            else "export_failed",
                            len(batch),
                        )
                        self.count("dropped", len(batch))
                except (httpx.HTTPError, ValueError, TypeError, json.JSONDecodeError):
                    self.count("export_failed", len(batch))
                    self.count("dropped", len(batch))
                finally:
                    for _ in batch:
                        self._queue.task_done()
