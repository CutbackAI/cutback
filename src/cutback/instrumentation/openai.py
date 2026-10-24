"""Pinned resource hooks. Preserves native responses and stream types; never reads text."""

import functools
import importlib
import os
import secrets
import sys
import time
from contextvars import ContextVar
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from opentelemetry import trace

from cutback.core.models import LLMEvent
from cutback.instrumentation import _identity
from cutback.instrumentation.exporter import Exporter

_operation: ContextVar[Any] = ContextVar("cutback_openai_operation", default=None)


def field(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def normalize_usage(response, surface):
    usage = field(response, "usage")
    if usage is None:
        return {"usage_source": "unavailable"}
    chat = surface == "chat.completions.create"
    inp = field(usage, "prompt_tokens" if chat else "input_tokens")
    out = field(usage, "completion_tokens" if chat else "output_tokens")
    if inp is None and out is None:
        return {"usage_source": "unavailable"}
    input_detail = field(usage, "prompt_tokens_details" if chat else "input_tokens_details")
    output_detail = field(usage, "completion_tokens_details" if chat else "output_tokens_details")
    return {
        "usage_source": "provider_reported",
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_tokens": field(input_detail, "cached_tokens"),
        "cache_write_tokens": field(input_detail, "cache_write_tokens"),
        "reasoning_tokens": field(output_detail, "reasoning_tokens"),
    }


def text_only(kwargs, surface):
    if (
        kwargs.get("tools")
        or kwargs.get("modalities")
        or kwargs.get("audio")
        or kwargs.get("extra_body")
    ):
        return False
    value = kwargs.get("messages" if surface.startswith("chat") else "input")
    if isinstance(value, str):
        return True
    if not isinstance(value, list):
        return False
    for item in value:
        if not isinstance(item, dict) or item.get("type", "message") != "message":
            return False
        content = item.get("content")
        if isinstance(content, str):
            continue
        if not isinstance(content, list):
            return False
        if any(
            not isinstance(part, dict) or part.get("type") not in {"text", "input_text"}
            for part in content
        ):
            return False
    return True


class Observation:
    def __init__(self, owner, resource, surface, kwargs):
        self.owner = owner
        self.surface = surface
        self.start = time.perf_counter()
        parent = trace.get_current_span().get_span_context()
        self.base = {
            "occurred_at": datetime.now(UTC),
            "service_name": owner.service_name,
            "environment": owner.environment,
            "provider": "openai",
            "sdk_surface": surface,
            "requested_model": kwargs.get("model"),
            "trace_id": f"{parent.trace_id:032x}" if parent.is_valid else secrets.token_hex(16),
            "span_id": secrets.token_hex(8),
            "parent_span_id": f"{parent.span_id:016x}" if parent.is_valid else None,
            "service_tier": kwargs.get("service_tier"),
            **(_identity.get() or {}),
        }
        client = resource._client
        self.client = client
        self.started_attempt = False
        self.logical_id = uuid4()
        official = client.base_url.host == "api.openai.com" and type(client).__name__ in {
            "OpenAI",
            "AsyncOpenAI",
        }
        self.base["metering_status"] = (
            "text_only" if official and text_only(kwargs, surface) else "unsupported_units"
        )
        self.response = None
        self.finished = False
        self.capture_input = (
            owner.capture.input_for(self.base, kwargs)
            if getattr(owner, "capture", None) and not kwargs.get("stream")
            else None
        )

    def attempt(self, number):
        self.started_attempt = True
        self.start = time.perf_counter()
        self.finished = False
        self.response = None
        self.base.update(
            occurred_at=datetime.now(UTC),
            retry_number=number,
            logical_request_id=self.logical_id,
            span_id=secrets.token_hex(8),
        )

    def chunk(self, chunk):
        if self.surface == "responses.create":
            if field(chunk, "type") in {
                "response.completed",
                "response.incomplete",
                "response.failed",
            }:
                self.response = field(chunk, "response")
        elif field(chunk, "usage") is not None:
            self.response = chunk

    def finish(self, *, response=None, error=None, incomplete=False, http_status=None):
        if self.finished or not self.started_attempt:
            return
        self.finished = True
        try:
            response = response if response is not None else self.response
            status: Literal["error", "incomplete", "success"] = (
                "error" if error else "incomplete" if incomplete else "success"
            )
            remote_status = field(response, "status")
            if remote_status in {"incomplete", "failed", "cancelled", "in_progress", "queued"}:
                status = "error" if remote_status == "failed" else "incomplete"
            event = LLMEvent(
                **self.base,
                response_model=field(response, "model"),
                duration_ms=(time.perf_counter() - self.start) * 1000,
                status=status,
                error_type=type(error).__name__ if error else None,
                http_status=http_status or getattr(error, "status_code", None),
                **self.owner.normalize_usage(response, self.surface),
            )
            self.owner.exporter.emit(event)
            if getattr(self.owner, "capture", None) and event.status == "success":
                self.owner.capture.emit(event, self.capture_input, response)
            if event.status == "success":
                self.capture_input = None
        except Exception:
            self.owner.exporter.count("normalization_failed")
            self.owner.exporter.count("dropped")


def wrap_stream(stream, observation, asynchronous):
    # Wrap the native iterator rather than replacing the Stream object or buffering content.
    iterator = stream._iterator
    close = stream.close
    if asynchronous:

        async def iterate():
            try:
                async for chunk in iterator:
                    try:
                        observation.chunk(chunk)
                    except Exception:
                        pass
                    yield chunk
            except BaseException as exc:
                observation.finish(error=exc)
                raise
            else:
                observation.finish(incomplete=observation.response is None)

        async def close_async():
            try:
                return await close()
            finally:
                observation.finish(incomplete=observation.response is None)

        stream._iterator = iterate()
        stream.close = close_async
    else:

        def iterate_sync():
            try:
                for chunk in iterator:
                    try:
                        observation.chunk(chunk)
                    except Exception:
                        pass
                    yield chunk
            except BaseException as exc:
                observation.finish(error=exc)
                raise
            else:
                observation.finish(incomplete=observation.response is None)

        def close_sync():
            try:
                return close()
            finally:
                observation.finish(incomplete=observation.response is None)

        stream._iterator = iterate_sync()
        stream.close = close_sync
    return stream


class OpenAIInstrumentation:
    enabled = True
    sdk_package = "openai"
    sdk_version = "2.29.0"
    observation_type = Observation
    normalize_usage = staticmethod(normalize_usage)

    def targets(self):
        from openai.resources.chat.completions import AsyncCompletions, Completions

        if self.sdk_version == "1.55.3":
            return [
                (Completions, "chat.completions.create", False),
                (AsyncCompletions, "chat.completions.create", True),
            ]
        from openai.resources.responses import AsyncResponses, Responses

        return [
            (Responses, "responses.create", False),
            (AsyncResponses, "responses.create", True),
            (Completions, "chat.completions.create", False),
            (AsyncCompletions, "chat.completions.create", True),
        ]

    def __init__(
        self,
        *,
        service_name,
        environment,
        endpoint,
        project_id,
        token,
        identity,
        capture=None,
        **export_options,
    ):
        installed = version(self.sdk_package)
        allowed = {"1.55.3", "2.29.0"} if self.sdk_package == "openai" else {self.sdk_version}
        if installed not in allowed:
            raise ValueError("unsupported SDK version: install the pinned Cutback provider extra")
        self.sdk_version = installed
        targets = self.targets()
        sdk_path = importlib.import_module(self.sdk_package).__file__
        if sdk_path is None:
            raise ValueError("SDK source cannot be verified")
        sdk_root = Path(sdk_path).resolve().parent
        if any(
            not hasattr(cls.create, "__code__")
            or not Path(cls.create.__code__.co_filename).resolve().is_relative_to(sdk_root)
            for cls, _, _ in targets
        ):
            raise ValueError(
                "existing SDK wrapper detected; choose one observer to prevent double counting"
            )
        # Validate identifiers before installing hooks or starting an exporter thread.
        from pydantic import TypeAdapter

        from cutback.core.models import Identifier

        TypeAdapter(Identifier).validate_python(service_name)
        TypeAdapter(Identifier).validate_python(environment)
        self.service_name, self.environment = service_name, environment
        self.identity = identity
        self.closed = False
        self.capture = capture
        self.exporter = Exporter(
            endpoint=endpoint, project_id=project_id, token=token, **export_options
        )
        if self.capture:
            self.exporter.extra_health = lambda: {**self.capture.counters, "capture_enabled": 1}
        self.patches = []
        for cls, surface, asynchronous in targets:
            original = cls.create
            wrapped = self._wrap(original, surface, asynchronous)
            cls.create = wrapped
            self.patches.append((cls, "create", original, wrapped))
        self._install_attempt_hooks()

    def _install_attempt_hooks(self):
        sdk_base = importlib.import_module(self.sdk_package + "._base_client")
        AsyncAPIClient, BaseClient, SyncAPIClient = (
            sdk_base.AsyncAPIClient,
            sdk_base.BaseClient,
            sdk_base.SyncAPIClient,
        )

        def patch(cls, method, factory):
            original = getattr(cls, method)
            wrapped = factory(original)
            setattr(cls, method, wrapped)
            self.patches.append((cls, method, original, wrapped))

        def build_wrapper(original):
            @functools.wraps(original)
            def build(client, *args, **kwargs):
                request = original(client, *args, **kwargs)
                obs = _operation.get()
                if obs is not None and obs.client is client:
                    try:
                        obs.attempt(kwargs.get("retries_taken", 0))
                    except Exception:
                        self.exporter.count("normalization_failed")
                return request

            return build

        def retry_wrapper(original, asynchronous):
            def record(client, kwargs):
                obs = _operation.get()
                if obs is not None and obs.client is client:
                    response = kwargs.get("response")
                    obs.finish(
                        error=sys.exception() or ConnectionError(),
                        http_status=getattr(response, "status_code", None)
                        or getattr(getattr(sys.exception(), "response", None), "status_code", None),
                    )

            if asynchronous:

                @functools.wraps(original)
                async def async_retry(client, *args, **kwargs):
                    try:
                        record(client, kwargs)
                    except Exception:
                        self.exporter.count("normalization_failed")
                    return await original(client, *args, **kwargs)

                return async_retry

            @functools.wraps(original)
            def sync_retry(client, *args, **kwargs):
                try:
                    record(client, kwargs)
                except Exception:
                    self.exporter.count("normalization_failed")
                return original(client, *args, **kwargs)

            return sync_retry

        patch(BaseClient, "_build_request", build_wrapper)
        retry_method = (
            "_sleep_for_retry" if hasattr(SyncAPIClient, "_sleep_for_retry") else "_retry_request"
        )
        patch(SyncAPIClient, retry_method, lambda fn: retry_wrapper(fn, False))
        patch(AsyncAPIClient, retry_method, lambda fn: retry_wrapper(fn, True))

    def _wrap(self, original, surface, asynchronous):
        def begin(resource, kwargs):
            if self.closed or os.getenv("CUTBACK_ENABLED", "true").lower() == "false":
                return None
            try:
                return self.observation_type(self, resource, surface, kwargs)
            except Exception:
                self.exporter.count("normalization_failed")
                return None

        def finish(result, obs):
            if obs is not None:
                try:
                    if hasattr(result, "_iterator"):
                        return wrap_stream(result, obs, asynchronous)
                    obs.finish(response=result)
                except Exception:
                    self.exporter.count("normalization_failed")
            return result

        if asynchronous:

            @functools.wraps(original)
            async def async_call(resource, *args, **kwargs):
                if _operation.get() is not None:
                    return await original(resource, *args, **kwargs)
                obs = begin(resource, kwargs)
                token = _operation.set(obs)
                try:
                    try:
                        result = await original(resource, *args, **kwargs)
                    except BaseException as exc:
                        if obs:
                            obs.finish(error=exc)
                        raise
                finally:
                    _operation.reset(token)
                return finish(result, obs)

            return async_call

        @functools.wraps(original)
        def sync_call(resource, *args, **kwargs):
            if _operation.get() is not None:
                return original(resource, *args, **kwargs)
            obs = begin(resource, kwargs)
            token = _operation.set(obs)
            try:
                try:
                    result = original(resource, *args, **kwargs)
                except BaseException as exc:
                    if obs:
                        obs.finish(error=exc)
                    raise
            finally:
                _operation.reset(token)
            return finish(result, obs)

        return sync_call

    def flush(self, timeout_seconds=0.3):
        started = time.monotonic()
        metadata = self.exporter.flush(timeout_seconds)
        captured = (
            self.capture.flush(max(0, timeout_seconds - (time.monotonic() - started)))
            if self.capture
            else True
        )
        return metadata and captured

    def health(self):
        return {
            "enabled": not self.closed and os.getenv("CUTBACK_ENABLED", "true").lower() != "false",
            **self.exporter.health(),
            **(self.capture.counters if self.capture else {}),
            "coverage": f"OpenAI {self.sdk_version}; SDK retry attempts; metadata events",
        }

    def close(self, timeout_seconds=0.3):
        self.closed = True
        for cls, method, original, wrapped in reversed(self.patches):
            if getattr(cls, method) is wrapped:
                setattr(cls, method, original)
        started = time.monotonic()
        metadata = self.exporter.close(timeout_seconds)
        captured = (
            self.capture.close(max(0, timeout_seconds - (time.monotonic() - started)))
            if self.capture
            else True
        )
        return metadata and captured
