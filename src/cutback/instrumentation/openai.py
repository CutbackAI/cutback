"""Pinned resource hooks. Preserves native responses and stream types; never reads text."""

import functools
import os
import secrets
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any, Literal

from opentelemetry import trace

from cutback.core.models import LLMEvent
from cutback.instrumentation import _identity
from cutback.instrumentation.exporter import Exporter


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
        official = client.base_url.host == "api.openai.com" and type(client).__name__ in {
            "OpenAI",
            "AsyncOpenAI",
        }
        self.base["metering_status"] = (
            "text_only" if official and text_only(kwargs, surface) else "unsupported_units"
        )
        self.response = None
        self.finished = False

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

    def finish(self, *, response=None, error=None, incomplete=False):
        if self.finished:
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
                http_status=getattr(error, "status_code", None),
                **normalize_usage(response, self.surface),
            )
            self.owner.exporter.emit(event)
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

    def __init__(
        self, *, service_name, environment, endpoint, project_id, token, identity, **export_options
    ):
        if version("openai") != "2.29.0":
            raise ValueError("unsupported SDK version: install the cutback[openai] pinned extra")
        from openai.resources.chat.completions import AsyncCompletions, Completions
        from openai.resources.responses import AsyncResponses, Responses

        targets: list[tuple[Any, str, bool]] = [
            (Responses, "responses.create", False),
            (AsyncResponses, "responses.create", True),
            (Completions, "chat.completions.create", False),
            (AsyncCompletions, "chat.completions.create", True),
        ]
        import openai

        sdk_root = Path(openai.__file__).resolve().parent
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
        self.exporter = Exporter(
            endpoint=endpoint, project_id=project_id, token=token, **export_options
        )
        self.patches = []
        for cls, surface, asynchronous in targets:
            original = cls.create
            wrapped = self._wrap(original, surface, asynchronous)
            cls.create = wrapped
            self.patches.append((cls, original, wrapped))

    def _wrap(self, original, surface, asynchronous):
        def begin(resource, kwargs):
            if self.closed or os.getenv("CUTBACK_ENABLED", "true").lower() == "false":
                return None
            try:
                return Observation(self, resource, surface, kwargs)
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
                obs = begin(resource, kwargs)
                try:
                    result = await original(resource, *args, **kwargs)
                except BaseException as exc:
                    if obs:
                        obs.finish(error=exc)
                    raise
                return finish(result, obs)

            return async_call

        @functools.wraps(original)
        def sync_call(resource, *args, **kwargs):
            obs = begin(resource, kwargs)
            try:
                result = original(resource, *args, **kwargs)
            except BaseException as exc:
                if obs:
                    obs.finish(error=exc)
                raise
            return finish(result, obs)

        return sync_call

    def flush(self, timeout_seconds=0.3):
        return self.exporter.flush(timeout_seconds)

    def health(self):
        return {
            "enabled": not self.closed and os.getenv("CUTBACK_ENABLED", "true").lower() != "false",
            **self.exporter.health(),
            "coverage": "OpenAI 2.29.0 resource operations; internal retries not separate attempts",
        }

    def close(self, timeout_seconds=0.3):
        self.closed = True
        for cls, original, wrapped in self.patches:
            if cls.create is wrapped:
                cls.create = original
        return self.exporter.close(timeout_seconds)
