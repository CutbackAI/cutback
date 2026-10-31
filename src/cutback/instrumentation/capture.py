"""Opt-in capture transport, separate from metadata. Policy checked before each outbound sample."""

import base64
import hashlib
import hmac
import json
import os
import queue
import threading
import time
from uuid import UUID, uuid4

import httpx
from pydantic import SecretBytes

from cutback.core.config import validate_endpoint
from cutback.core.native_request_profile import NativeRequestProfileSnapshot
from cutback.evals.datasets import CapturePolicy, Sample, digest, redact


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
        if not 0.01 <= timeout <= 2:
            raise ValueError("capture timeout must be 0.01–2 seconds")
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
        self._profile_key = None
        self._profile_key_id = None
        self._profile_clock_domain_id = uuid4()
        self._profile_identity = None
        self._profile_lock = threading.Lock()
        if self.policy.native_request_profile is not None:
            try:
                encoded = os.getenv("CUTBACK_NATIVE_REQUEST_PROFILE_HMAC_KEY", "")
                if not encoded or len(encoded) > 512:
                    raise ValueError("NATIVE_PROFILE_KEY_REQUIRED")
                key = base64.b64decode(encoded, validate=True)
                if not 32 <= len(key) <= 128:
                    raise ValueError("NATIVE_PROFILE_KEY_BOUND")
                self._profile_key = key
                self._profile_key_id = UUID(
                    bytes=hmac.new(
                        key, b"cutback-native-profile-key-id-v1", hashlib.sha256
                    ).digest()[:16],
                    version=4,
                )
            except Exception:
                self.counters["native_profile_unconfigured"] = 1
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

    def request_profile_for(self, base, kwargs, client):
        if self.policy.native_request_profile is None:
            return None
        if self.closed.is_set() or not self._profile_lock.acquire(blocking=False):
            self.counters["native_profile_busy"] = self.counters.get("native_profile_busy", 0) + 1
            return None
        started = time.perf_counter()
        try:
            return self._request_profile_for_locked(base, kwargs, client)
        finally:
            self.counters["native_profile_overhead_us_total"] = self.counters.get(
                "native_profile_overhead_us_total", 0
            ) + max(0, int((time.perf_counter() - started) * 1000000))
            if self.closed.is_set():
                self._clear_profile_identity()
            self._profile_lock.release()

    def _clear_profile_identity(self):
        if self._profile_identity is not None:
            self._profile_identity.close()
            self._profile_identity = None
        self._profile_key = None

    def _request_profile_for_locked(self, base, kwargs, client):
        policy = self.policy
        profile = policy.native_request_profile
        if profile is None or self._profile_key is None:
            return None
        if (
            policy.never_capture_fields
            or base.get("provider") != "openai"
            or base.get("sdk_surface") != "responses.create"
            or base.get("metering_status") != "text_only"
            or not policy.permits(base["environment"], base.get("workflow_id"))
            or os.getenv("CUTBACK_CAPTURE_DENY", "false").lower() == "true"
        ):
            self.counters["native_profile_unsupported"] = (
                self.counters.get("native_profile_unsupported", 0) + 1
            )
            return None
        try:
            from cutback.native_cache import NativeCacheContext, NativeResponseCacheExecution

            if self._profile_identity is None:
                self._profile_identity = NativeResponseCacheExecution(
                    NativeCacheContext(
                        project_id=self.project_id,
                        policy_id=self.policy_id,
                        request_hmac_key=SecretBytes(self._profile_key),
                        workflow=base["workflow_id"],
                        environment=base["environment"],
                        prompt_revision="native-request-profile-v1",
                        freshness_ref=profile.freshness_ref,
                        ttl_seconds=profile.prospective_ttl_seconds,
                        content_retention_authorized=True,
                        immutable_or_safely_invalidated=True,
                    )
                )
            identity, sdk_version = self._profile_identity.request_identity(client, **kwargs)
            return {
                "capture_policy_sha256": digest(policy.model_dump(mode="json")),
                "profile_key_id": self._profile_key_id,
                "clock_domain_id": self._profile_clock_domain_id,
                "request_hmac_sha256": identity["key_sha256"],
                "sdk_version": sdk_version,
            }
        except Exception:
            self.counters["native_profile_unsupported"] = (
                self.counters.get("native_profile_unsupported", 0) + 1
            )
            return None

    def emit(self, event, input_text, response, *, native_request_profile=None):
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
        profile = None
        if native_request_profile is not None:
            try:
                from cutback.native_cache import NativeResponseCacheExecution

                profile = NativeRequestProfileSnapshot.model_validate(
                    {
                        **native_request_profile,
                        "response_cache_eligible": NativeResponseCacheExecution._eligible(response),
                    }
                )
            except Exception:
                self.counters["native_profile_dropped"] = (
                    self.counters.get("native_profile_dropped", 0) + 1
                )
        sample = Sample(
            sample_id=event.event_id,
            event_id=event.event_id,
            policy_id=self.policy_id,
            environment=event.environment,
            workflow=event.workflow_id,
            provider=event.provider,
            input=input_text,
            reference=redact(output, self.policy.never_capture_fields),
            native_request_profile=profile,
        )
        try:
            self.queue.put_nowait(sample)
            self.counters["captured_queued"] += 1
            if profile is not None:
                self.counters["native_profile_queued"] = (
                    self.counters.get("native_profile_queued", 0) + 1
                )
        except queue.Full:
            self.counters["captured_dropped"] += 1
            if profile is not None:
                self.counters["native_profile_dropped"] = (
                    self.counters.get("native_profile_dropped", 0) + 1
                )

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
                    self.policy = policy
                    if not policy.permits(sample.environment, sample.workflow):
                        raise ValueError("CAPTURE_POLICY_REVOKED")
                    response = client.post(
                        self.base + "/samples", json=sample.model_dump(mode="json")
                    )
                    if response.status_code not in {200, 201}:
                        raise ValueError("CAPTURE_REJECTED")
                    self.counters["captured_exported"] += 1
                    if sample.native_request_profile is not None:
                        self.counters["native_profile_exported"] = (
                            self.counters.get("native_profile_exported", 0) + 1
                        )
                except Exception:
                    self.counters["captured_dropped"] += 1
                    if sample.native_request_profile is not None:
                        self.counters["native_profile_dropped"] = (
                            self.counters.get("native_profile_dropped", 0) + 1
                        )
                finally:
                    self.queue.task_done()

    def flush(self, timeout=0.3):
        deadline = time.monotonic() + max(0, min(timeout, 5))
        while self.queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.002)
        return self.queue.unfinished_tasks == 0

    def close(self, timeout=0.3):
        self.closed.set()
        self._profile_key = None
        if self._profile_lock.acquire(blocking=False):
            try:
                self._clear_profile_identity()
            finally:
                self._profile_lock.release()
        return self.flush(timeout)
