"""Explicit BYOK model execution. No request retries, tool execution or arbitrary URLs."""

import os
import time
from dataclasses import replace
from datetime import UTC, datetime

from cutback.core.models import LLMEvent
from cutback.evals.experiments import Execution, route_target
from cutback.pricing import Catalog


class OpenAIExecutor:
    def __init__(self, *, http_client=None):
        key = os.getenv("CUTBACK_EXPERIMENT_OPENAI_KEY")
        if not key:
            raise ValueError("NO_EXPERIMENT_API_KEY")
        from importlib.metadata import version

        if version("openai") != "2.29.0":
            raise ValueError("UNSUPPORTED_EXPERIMENT_SDK_VERSION")
        import httpx
        from openai import OpenAI

        self.client = OpenAI(
            api_key=key,
            max_retries=0,
            base_url="https://api.openai.com/v1",
            http_client=http_client or httpx.Client(trust_env=False, follow_redirects=False),
        )
        self.client.organization = None
        self.client.project = None

    def execute(self, model, text, timeout_seconds):
        if model.provider != "openai":
            raise ValueError("EXECUTOR_PROVIDER_UNSUPPORTED")
        started = time.perf_counter()
        response = self.client.responses.create(
            model=model.model,
            input=text,
            instructions=model.instruction or None,
            max_output_tokens=model.max_output_tokens,
            store=False,
            timeout=timeout_seconds,
        )
        latency = (time.perf_counter() - started) * 1000
        from cutback.instrumentation.openai import normalize_usage

        event = LLMEvent(
            environment="experiment",
            service_name="cutback-worker",
            provider="openai",
            sdk_surface="responses.create",
            requested_model=model.model,
            response_model=response.model,
            trace_id="1" * 32,
            span_id="1" * 16,
            occurred_at=datetime.now(UTC),
            duration_ms=latency,
            status="success" if response.status == "completed" else "incomplete",
            metering_status="text_only",
            **normalize_usage(response, "responses.create"),
        )
        cost = Catalog().price(event)
        return Execution(
            response.output_text,
            cost.amount,
            latency,
            event.input_tokens,
            event.output_tokens,
            complete=response.status == "completed",
        )

    def close(self):
        self.client.close()


class LocalExecutor:
    """Explicit customer Chat endpoint; no tool execution or dataset-supplied URLs."""

    def __init__(self):
        from cutback.core.config import validate_endpoint

        endpoint = os.getenv("CUTBACK_LOCAL_INFERENCE_ENDPOINT")
        if not endpoint:
            raise ValueError("NO_LOCAL_INFERENCE_ENDPOINT")
        import httpx
        from openai import OpenAI

        self.client = OpenAI(
            base_url=validate_endpoint(endpoint),
            api_key=os.getenv("CUTBACK_LOCAL_INFERENCE_KEY", "local-inference-no-auth"),
            max_retries=0,
            http_client=httpx.Client(trust_env=False, follow_redirects=False),
        )

    def execute(self, model, text, timeout_seconds):
        if model.provider != "local":
            raise ValueError("EXECUTOR_PROVIDER_UNSUPPORTED")
        started = time.perf_counter()
        from openai.types.chat import ChatCompletionMessageParam

        messages: list[ChatCompletionMessageParam] = []
        if model.instruction:
            messages.append({"role": "system", "content": model.instruction})
        messages.append({"role": "user", "content": text})
        response = self.client.chat.completions.create(
            model=model.model,
            messages=messages,
            max_tokens=model.max_output_tokens,
            timeout=timeout_seconds,
        )
        return Execution(
            response.choices[0].message.content or "",
            model.local_cost_upper_usd,
            (time.perf_counter() - started) * 1000,
            complete=response.choices[0].finish_reason == "stop",
        )

    def close(self):
        self.client.close()


class ConfiguredExecutor:
    def __init__(self):
        self.clients = {}

    def execute(self, model, text, timeout_seconds):
        if model.provider == "router":
            target = route_target(model, text)
            return replace(
                self.execute(target, text, timeout_seconds),
                actual_model=target.provider + ":" + target.model,
            )
        if model.provider not in self.clients:
            self.clients[model.provider] = {
                "openai": OpenAIExecutor,
                "anthropic": AnthropicExecutor,
                "local": LocalExecutor,
            }[model.provider]()
        return self.clients[model.provider].execute(model, text, timeout_seconds)

    def close(self):
        for client in self.clients.values():
            client.close()


def anthropic_transport():
    import httpx2

    return httpx2.Client(trust_env=False, follow_redirects=False)


class AnthropicExecutor:
    def __init__(self):
        from importlib.metadata import version

        key = os.getenv("CUTBACK_EXPERIMENT_ANTHROPIC_KEY")
        if not key:
            raise ValueError("NO_ANTHROPIC_EXPERIMENT_KEY")
        if version("anthropic") != "2.3.0":
            raise ValueError("UNSUPPORTED_EXPERIMENT_SDK_VERSION")
        from anthropic import Anthropic

        self.client = Anthropic(
            api_key=key,
            max_retries=0,
            base_url="https://api.anthropic.com",
            http_client=anthropic_transport(),
        )
        # 2.3.0 merges ANTHROPIC_CUSTOM_HEADERS even with an explicit key. This owned,
        # pinned worker client accepts no ambient application headers or billing context.
        self.client._custom_headers = {}

    def execute(self, model, text, timeout_seconds):
        if model.provider != "anthropic":
            raise ValueError("EXECUTOR_PROVIDER_UNSUPPORTED")
        started = time.perf_counter()
        params = {
            "model": model.model,
            "max_tokens": model.max_output_tokens,
            "messages": [{"role": "user", "content": text}],
            "timeout": timeout_seconds,
        }
        if model.instruction:
            params["system"] = (
                [
                    {
                        "type": "text",
                        "text": model.instruction,
                        "cache_control": {
                            "type": "ephemeral",
                            "ttl": "1h" if model.prompt_cache == "anthropic-1h" else "5m",
                        },
                    }
                ]
                if model.prompt_cache
                else model.instruction
            )
        response = self.client.messages.create(**params)
        from cutback.instrumentation.anthropic import normalize_usage

        complete = response.stop_reason in {"end_turn", "stop_sequence"}
        event = LLMEvent(
            environment="experiment",
            service_name="cutback-worker",
            provider="anthropic",
            sdk_surface="messages.create",
            requested_model=model.model,
            response_model=response.model,
            trace_id="1" * 32,
            span_id="1" * 16,
            occurred_at=datetime.now(UTC),
            duration_ms=(time.perf_counter() - started) * 1000,
            status="success" if complete else "incomplete",
            metering_status="text_only",
            **normalize_usage(response, "messages.create"),
        )
        cost = Catalog().price(event)
        output = "".join(block.text for block in response.content if block.type == "text")
        return Execution(
            output,
            cost.amount,
            event.duration_ms,
            event.input_tokens,
            event.output_tokens,
            complete,
        )

    def close(self):
        self.client.close()
