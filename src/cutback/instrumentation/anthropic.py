"""Anthropic 2.3.0 Messages hooks; native stream and cache usage normalization."""

from cutback.instrumentation.openai import Observation, OpenAIInstrumentation, field, text_only


def normalize_usage(response, surface):
    usage = field(response, "usage")
    if usage is None:
        return {"usage_source": "unavailable"}
    creation = field(usage, "cache_creation")
    hour = field(creation, "ephemeral_1h_input_tokens")
    total_write = field(usage, "cache_creation_input_tokens")
    five = field(creation, "ephemeral_5m_input_tokens")
    # Old SDK/server responses omit duration detail. Never price an ambiguous TTL as five minutes.
    if five is None and total_write == 0:
        five, hour = 0, 0
    return {
        "usage_source": "provider_reported",
        "input_tokens": field(usage, "input_tokens"),
        "output_tokens": field(usage, "output_tokens"),
        "cache_read_tokens": field(usage, "cache_read_input_tokens"),
        "cache_write_tokens": five,
        "cache_write_1h_tokens": hour,
    }


class AnthropicObservation(Observation):
    def __init__(self, owner, resource, surface, kwargs):
        super().__init__(owner, resource, surface, kwargs)
        self.base["provider"] = "anthropic"
        self.base["region"] = kwargs.get("inference_geo")
        if kwargs.get("speed") == "fast":
            self.base["service_tier"] = "fast"
        self.base["metering_status"] = (
            "text_only"
            if resource._client.base_url.host == "api.anthropic.com"
            and text_only(kwargs, "chat.completions.create")
            else "unsupported_units"
        )
        self._stream_usage = None
        self._stream_model = None
        if getattr(owner, "capture", None) and not kwargs.get("stream"):
            self.capture_input = owner.capture.input_for(self.base, kwargs)

    def chunk(self, chunk):
        kind = field(chunk, "type")
        if kind == "message_start":
            message = field(chunk, "message")
            self._stream_model = field(message, "model")
            usage = field(message, "usage")
            keys = (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
                "cache_creation",
            )
            self._stream_usage = {key: field(usage, key) for key in keys}
        elif kind == "message_delta" and self._stream_usage is not None:
            usage = field(chunk, "usage")
            for key in (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
                "cache_creation",
            ):
                value = field(usage, key)
                if value is not None:
                    self._stream_usage[key] = value
        elif kind == "message_stop" and self._stream_usage is not None:
            self.response = {"model": self._stream_model, "usage": self._stream_usage}


class AnthropicInstrumentation(OpenAIInstrumentation):
    sdk_package = "anthropic"
    sdk_version = "2.3.0"
    observation_type = AnthropicObservation
    normalize_usage = staticmethod(normalize_usage)

    def targets(self):
        from anthropic.resources.messages import AsyncMessages, Messages

        return [(Messages, "messages.create", False), (AsyncMessages, "messages.create", True)]

    def health(self):
        return {
            **super().health(),
            "coverage": "Anthropic 2.3.0 Messages.create sync/async/stream/retries; metadata only",
        }
