"""Explicit BYOK model execution. No request retries, tool execution or arbitrary URLs."""

import os
import time
from datetime import UTC, datetime

from cutback.core.models import LLMEvent
from cutback.evals.experiments import Execution
from cutback.pricing import Catalog


class OpenAIExecutor:
    def __init__(self):
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
            http_client=httpx.Client(trust_env=False, follow_redirects=False),
        )

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
            response.output_text, cost.amount, latency, event.input_tokens, event.output_tokens
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
        )

    def close(self):
        self.client.close()


class ConfiguredExecutor:
    def __init__(self):
        self.clients = {}

    def execute(self, model, text, timeout_seconds):
        if model.provider not in self.clients:
            self.clients[model.provider] = (
                OpenAIExecutor() if model.provider == "openai" else LocalExecutor()
            )
        return self.clients[model.provider].execute(model, text, timeout_seconds)

    def close(self):
        for client in self.clients.values():
            client.close()
