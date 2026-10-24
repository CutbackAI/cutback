"""Opt-in capture transport, separate from metadata. Policy checked before each outbound sample."""

import json
import os
import queue
import threading
import time
from uuid import UUID

import httpx

from cutback.core.config import validate_endpoint
from cutback.evals.datasets import CapturePolicy, Sample, redact


def sampled(policy, sample_id):
    return UUID(str(sample_id)).int / (2**128) < policy.sample_rate


def bounded_content(value, limit):
    """Reject oversized/deep input before copying, redacting or serializing it."""
    remaining = limit
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        remaining -= 1
        if remaining < 0 or depth > 20:
            return False
        if isinstance(item, str):
            remaining -= len(item)
        elif isinstance(item, (dict, list)):
            if len(item) > remaining:
                return False
            if isinstance(item, dict):
                pending.extend((key, depth + 1) for key in item)
                pending.extend((part, depth + 1) for part in item.values())
            else:
                pending.extend((part, depth + 1) for part in item)
        elif item is not None and not isinstance(item, (bool, int, float)):
            return False
    return remaining >= 0


class CaptureExporter:
    def __init__(self, endpoint, project_id, token, policy_id, *, timeout=0.3):
        if not endpoint.endswith("/v1/ingest/events"):
            raise ValueError("capture requires the standard collector endpoint")
        self.base = validate_endpoint(endpoint).removesuffix("/events")
        self.project_id = UUID(str(project_id))
        self.policy_id = UUID(str(policy_id))
        self._token = token
        self._timeout = timeout
        self.queue: queue.Queue = queue.Queue(16)
        self.closed = threading.Event()
        self.counters = {"captured_queued": 0, "captured_exported": 0, "captured_dropped": 0}
        # Explicit capture validates policy; metadata-only init makes no network call.
        with self.client() as client:
            self.policy = self.fetch_policy(client)
        if not self.policy.enabled:
            raise ValueError("CAPTURE_POLICY_DISABLED")
        self.thread = threading.Thread(target=self._run, daemon=True, name="cutback-capture")
        self.thread.start()

    def client(self):
        return httpx.Client(
            timeout=self._timeout,
            follow_redirects=False,
            trust_env=False,
            headers={"Authorization": "Bearer " + self._token},
        )

    def fetch_policy(self, client):
        response = client.get(self.base + "/capture-policies/" + str(self.policy_id))
        if response.status_code != 200:
            raise ValueError("CAPTURE_POLICY_NOT_AUTHORIZED")
        return CapturePolicy.model_validate(response.json()["policy"])

    def input_for(self, base, kwargs):
        if os.getenv("CUTBACK_CAPTURE_DENY", "false").lower() == "true":
            return None
        workflow = base.get("workflow_id")
        if not workflow or not self.policy.permits(base["environment"], workflow):
            return None
        if base["provider"] not in self.policy.allowed_providers:
            return None
        if base.get("metering_status") != "text_only" or any(
            field in self.policy.never_capture_fields for field in ("input", "messages")
        ):
            return None
        value = kwargs.get("input", kwargs.get("messages"))
        if not isinstance(value, (str, list)):
            return None
        if not bounded_content(value, self.policy.max_payload_bytes):
            return None
        value = redact(value, self.policy.never_capture_fields)
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        instruction = kwargs.get("instructions", kwargs.get("system"))
        if any(
            key in self.policy.never_capture_fields
            for key in ("instructions", "instruction", "system")
        ):
            instruction = None
        if instruction:
            if not bounded_content(instruction, self.policy.max_payload_bytes):
                return None
            text = json.dumps({"instruction": instruction, "input": text}, ensure_ascii=False)
        if len(text.encode()) > self.policy.max_payload_bytes:
            return None
        return redact(text, self.policy.never_capture_fields)

    def emit(self, event, input_text, response):
        if self.closed.is_set() or input_text is None or not sampled(self.policy, event.event_id):
            return
        deny_output = any(k in self.policy.never_capture_fields for k in ("output", "reference"))
        output = None if deny_output else getattr(response, "output_text", None)
        if output is None and not deny_output:
            blocks = getattr(response, "content", None)
            if isinstance(blocks, list):
                output = "".join(getattr(part, "text", "") for part in blocks)
        if output is not None and len(output.encode()) > self.policy.max_payload_bytes:
            self.counters["captured_dropped"] += 1
            return
        sample = Sample(
            sample_id=event.event_id,
            event_id=event.event_id,
            policy_id=self.policy_id,
            environment=event.environment,
            workflow=event.workflow_id,
            provider=event.provider,
            input=input_text,
            reference=redact(output, self.policy.never_capture_fields),
        )
        try:
            self.queue.put_nowait(sample)
            self.counters["captured_queued"] += 1
        except queue.Full:
            self.counters["captured_dropped"] += 1

    def _run(self):
        with self.client() as client:
            while not self.closed.is_set() or not self.queue.empty():
                try:
                    sample = self.queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                try:
                    if os.getenv("CUTBACK_CAPTURE_DENY", "false").lower() == "true":
                        raise ValueError("GLOBAL_CAPTURE_DENY")
                    policy = self.fetch_policy(client)
                    if not policy.permits(sample.environment, sample.workflow):
                        raise ValueError("CAPTURE_POLICY_REVOKED")
                    response = client.post(
                        self.base + "/samples", json=sample.model_dump(mode="json")
                    )
                    if response.status_code not in {200, 201}:
                        raise ValueError("CAPTURE_REJECTED")
                    self.counters["captured_exported"] += 1
                except Exception:
                    self.counters["captured_dropped"] += 1
                finally:
                    self.queue.task_done()

    def flush(self, timeout=0.3):
        deadline = time.monotonic() + max(0, min(timeout, 5))
        while self.queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.002)
        return self.queue.unfinished_tasks == 0

    def close(self, timeout=0.3):
        self.closed.set()
        return self.flush(timeout)
