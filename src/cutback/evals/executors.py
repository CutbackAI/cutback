"""Explicit BYOK model execution. No request retries, tool execution or arbitrary URLs."""

import os
import time
from dataclasses import replace
from datetime import UTC, datetime

from cutback.core.model_identity import reported_model_identifier
from cutback.core.models import LLMEvent
from cutback.core.sdk_compat import OPENAI_TEXT_SDKS
from cutback.evals._logging import owned_sdk_request
from cutback.evals.experiments import Execution, qualified_native_model, route_target
from cutback.pricing import Catalog


def chat_text_result(response):
    if not response.choices:
        return "", False, False
    message = response.choices[0].message
    unsupported = bool(
        getattr(message, "tool_calls", None)
        or getattr(message, "function_call", None)
        or getattr(message, "audio", None)
    )
    complete = (
        len(response.choices) == 1
        and response.choices[0].finish_reason == "stop"
        and not getattr(message, "refusal", None)
        and not unsupported
    )
    return message.content or "", bool(complete), unsupported


def require_single_call_model(model):
    if getattr(model, "agent_turn", None) is not None:
        raise ValueError("AGENT_TURN_REQUIRES_QUALIFIED_TURN_EXECUTOR")


class OpenAIExecutor:
    synthetic_transport = False

    def __init__(self, *, http_client=None):
        key = os.getenv("CUTBACK_EXPERIMENT_OPENAI_KEY")
        if not key:
            raise ValueError("NO_EXPERIMENT_API_KEY")
        from importlib.metadata import version

        if version("openai") not in OPENAI_TEXT_SDKS:
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

    @owned_sdk_request
    def execute(self, model, text, timeout_seconds):
        if model.provider != "openai":
            raise ValueError("EXECUTOR_PROVIDER_UNSUPPORTED")
        qualified_native_model(model)
        if model.native_contract is not None and not isinstance(text, str):
            raise ValueError("MODERN_NATIVE_TEXT_INPUT_REQUIRED")
        require_single_call_model(model)
        started = time.perf_counter()
        contract = model.request_contract
        payload = contract.render(text) if contract else text
        bundle = contract is not None and contract.version == "native-prompt-v1"
        if bundle and model.instruction:
            raise ValueError("BOUND_PROMPT_INSTRUCTIONS_MUST_BE_IN_CONTRACT")
        options = dict(contract.options) if contract else {}
        if model.native_contract is not None:
            options.update(
                reasoning={"effort": model.native_contract.reasoning_effort},
                service_tier=model.native_contract.service_tier,
                extra_body={
                    "prompt_cache_options": {"ttl": model.native_contract.cache_write_lifetime}
                },
            )
        from openai.types.chat import ChatCompletion
        from openai.types.responses import Response

        response: ChatCompletion | Response
        chat = contract is not None and contract.sdk_surface == "chat.completions.create"
        if contract and contract.sdk_surface not in {"responses.create", "chat.completions.create"}:
            raise ValueError("EXECUTOR_REQUEST_SURFACE_UNSUPPORTED")
        if chat:
            if model.instruction:
                raise ValueError("CHAT_INSTRUCTIONS_BELONG_IN_BOUND_MESSAGES")
            response = self.client.chat.completions.create(
                model=model.model,
                messages=payload["messages"] if bundle else payload,
                max_tokens=model.max_output_tokens,
                store=False,
                timeout=timeout_seconds,
                **options,
            )
            output, complete, unsupported = chat_text_result(response)
        else:
            response = self.client.responses.create(
                model=model.model,
                input=payload["input"] if bundle else payload,
                instructions=payload.get("instructions") if bundle else model.instruction or None,
                max_output_tokens=model.max_output_tokens,
                store=False,
                timeout=timeout_seconds,
                **options,
            )
            complete, output = response.status == "completed", response.output_text
            unsupported = any(
                item.type not in {"message", "reasoning"}
                or item.type == "message"
                and any(block.type not in {"output_text", "refusal"} for block in item.content)
                for item in response.output or []
            )
            refused = any(
                item.type == "message" and any(block.type == "refusal" for block in item.content)
                for item in response.output or []
            )
            complete = complete and not unsupported and not refused
        latency = (time.perf_counter() - started) * 1000
        from cutback.instrumentation.openai import normalize_usage

        event = LLMEvent(
            environment="experiment",
            service_name="cutback-worker",
            provider="openai",
            sdk_surface="chat.completions.create" if chat else "responses.create",
            requested_model=model.model,
            response_model=response.model,
            service_tier=getattr(response, "service_tier", None) or options.get("service_tier"),
            trace_id="1" * 32,
            span_id="1" * 16,
            occurred_at=datetime.now(UTC),
            duration_ms=latency,
            status="success" if complete else "incomplete",
            metering_status="unsupported_units" if unsupported else "text_only",
            **normalize_usage(response, "chat.completions.create" if chat else "responses.create"),
        )
        cost = Catalog().price(event)
        if model.native_contract is not None and getattr(response, "service_tier", None) not in {
            "default",
        }:
            # Auto can resolve to a premium tier. Missing actual-tier evidence is
            # insufficient for this new paid contract, even when token counts exist.
            cost = replace(
                cost, amount=None, status="unpriced", reason="native_served_tier_unqualified"
            )
        if model.native_contract is not None and response.model != model.model:
            cost = replace(
                cost, amount=None, status="unpriced", reason="native_response_model_scope_mismatch"
            )
        return Execution(
            output,
            cost.amount,
            latency,
            event.input_tokens,
            event.output_tokens,
            complete=complete,
            actual_model="openai:" + response.model if model.native_contract is not None else None,
            response_model=reported_model_identifier(response.model),
        )

    def close(self):
        self.client.close()


class LocalExecutor:
    """Explicit customer Chat endpoint; no tool execution or dataset-supplied URLs."""

    def __init__(self, *, http_client=None):
        from importlib.metadata import version

        from cutback.core.config import validate_endpoint

        if version("openai") not in OPENAI_TEXT_SDKS:
            raise ValueError("UNSUPPORTED_EXPERIMENT_SDK_VERSION")
        endpoint = os.getenv("CUTBACK_LOCAL_INFERENCE_ENDPOINT")
        if not endpoint:
            raise ValueError("NO_LOCAL_INFERENCE_ENDPOINT")
        import httpx
        from openai import OpenAI

        self.client = OpenAI(
            base_url=validate_endpoint(endpoint),
            api_key=os.getenv("CUTBACK_LOCAL_INFERENCE_KEY", "local-inference-no-auth"),
            max_retries=0,
            http_client=http_client or httpx.Client(trust_env=False, follow_redirects=False),
        )
        self.client.organization = None
        self.client.project = None

    @owned_sdk_request
    def execute(self, model, text, timeout_seconds):
        if model.provider != "local":
            raise ValueError("EXECUTOR_PROVIDER_UNSUPPORTED")
        require_single_call_model(model)
        started = time.perf_counter()
        from openai.types.chat import ChatCompletionMessageParam

        messages: list[ChatCompletionMessageParam] = []
        if model.instruction:
            messages.append({"role": "system", "content": model.instruction})
        messages.append({"role": "user", "content": text})
        options = {}
        if model.request_contract:
            contract = model.request_contract
            if contract.sdk_surface != "chat.completions.create":
                raise ValueError("EXECUTOR_REQUEST_SURFACE_UNSUPPORTED")
            if model.instruction:
                raise ValueError("CHAT_INSTRUCTIONS_BELONG_IN_BOUND_MESSAGES")
            rendered = contract.render(text)
            messages = rendered["messages"] if contract.version == "native-prompt-v1" else rendered
            options = contract.options
        response = self.client.chat.completions.create(
            model=model.model,
            messages=messages,
            max_tokens=model.max_output_tokens,
            timeout=timeout_seconds,
            **options,
        )
        output, complete, _ = chat_text_result(response)
        return Execution(
            output,
            model.local_cost_upper_usd,
            (time.perf_counter() - started) * 1000,
            complete=complete,
            response_model=reported_model_identifier(response.model),
        )

    def close(self):
        self.client.close()


class ConfiguredExecutor:
    def __init__(self):
        self.clients = {}

    def prepare(self, provider):
        """Load a configured client before reserving an online judge request."""
        if provider not in self.clients:
            constructor = {
                "openai": OpenAIExecutor,
                "anthropic": AnthropicExecutor,
                "local": LocalExecutor,
            }.get(provider)
            if constructor is None:
                raise ValueError("EXECUTOR_PROVIDER_UNSUPPORTED")
            self.clients[provider] = constructor()

    def execute(self, model, text, timeout_seconds):
        require_single_call_model(model)
        if model.provider == "router":
            target = route_target(model, text)
            result = self.execute(target, text, timeout_seconds)
            return replace(
                result,
                actual_model=result.actual_model or target.provider + ":" + target.model,
            )
        self.prepare(model.provider)
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

    @owned_sdk_request
    def execute(self, model, text, timeout_seconds):
        if model.provider != "anthropic":
            raise ValueError("EXECUTOR_PROVIDER_UNSUPPORTED")
        require_single_call_model(model)
        started = time.perf_counter()
        params = {
            "model": model.model,
            "max_tokens": model.max_output_tokens,
            "messages": model.request_contract.render(text)
            if model.request_contract
            else [{"role": "user", "content": text}],
            "timeout": timeout_seconds,
        }
        if model.request_contract:
            if model.request_contract.sdk_surface != "messages.create":
                raise ValueError("EXECUTOR_REQUEST_SURFACE_UNSUPPORTED")
            params.update(model.request_contract.options)
            if model.request_contract.version == "native-prompt-v1":
                if model.instruction:
                    raise ValueError("BOUND_PROMPT_INSTRUCTIONS_MUST_BE_IN_CONTRACT")
                bundle = params["messages"]
                params["messages"] = bundle["messages"]
                if "system" in bundle:
                    params["system"] = [dict(block) for block in bundle["system"]]
                    if model.prompt_cache:
                        params["system"][-1]["cache_control"] = {
                            "type": "ephemeral",
                            "ttl": "1h" if model.prompt_cache == "anthropic-1h" else "5m",
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

        unsupported = any(
            block.type not in {"text", "thinking", "redacted_thinking"}
            for block in response.content
        )
        complete = response.stop_reason in {"end_turn", "stop_sequence"} and not unsupported
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
            metering_status="unsupported_units" if unsupported else "text_only",
            **normalize_usage(response, "messages.create"),
        )
        cost = Catalog().price(event)
        output = "".join(block.text for block in response.content if block.type == "text")
        assert event.duration_ms is not None
        return Execution(
            output,
            cost.amount,
            event.duration_ms,
            event.input_tokens,
            event.output_tokens,
            complete,
            response_model=reported_model_identifier(response.model),
        )

    def close(self):
        self.client.close()
