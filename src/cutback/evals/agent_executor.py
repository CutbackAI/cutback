"""Pinned synchronous, nonstreaming native function turns. No application tools run here."""

import json
import time
from datetime import UTC, datetime
from decimal import Decimal

from cutback.core.agent_turns import EPISODE_FINAL_SCHEMA
from cutback.core.episodes import EpisodeAssistant, EpisodeCitation, EpisodeToolCall
from cutback.core.models import LLMEvent
from cutback.evals._logging import owned_sdk_request
from cutback.evals.executors import ConfiguredExecutor
from cutback.evals.experiments import Execution
from cutback.evals.output_contracts import output_contract
from cutback.pricing import Catalog


def strict_json(text):
    if len(text.encode()) > 64000:
        raise ValueError("AGENT_NATIVE_CONTENT_BOUND")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("AGENT_DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError("AGENT_NONFINITE_JSON_NUMBER")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)


class NativeAgentExecutor(ConfiguredExecutor):
    supports_agent_turns = True
    synthetic_transport = False

    @owned_sdk_request
    def execute(self, model, text, timeout_seconds):
        frame = model.agent_turn
        if frame is None:
            return super().execute(model, text, timeout_seconds)
        if model.provider not in {"openai", "anthropic"}:
            raise ValueError("NATIVE_AGENT_PROVIDER_UNQUALIFIED")
        self.prepare(model.provider)
        client = self.clients[model.provider].client
        started = time.perf_counter()
        payload = frame.native_payload()
        if model.provider == "anthropic":
            response = client.messages.create(
                model=model.model,
                max_tokens=model.max_output_tokens,
                timeout=timeout_seconds,
                **payload,
            )
            from cutback.instrumentation.anthropic import normalize_usage
        else:
            if frame.surface == "chat.completions.create":
                response = client.chat.completions.create(
                    model=model.model,
                    max_tokens=model.max_output_tokens,
                    timeout=timeout_seconds,
                    **payload,
                )
            else:
                response = client.responses.create(
                    model=model.model,
                    max_output_tokens=model.max_output_tokens,
                    timeout=timeout_seconds,
                    **payload,
                )
            from cutback.instrumentation.openai import normalize_usage
        elapsed = (time.perf_counter() - started) * 1000
        raw, calls, finish, unsupported = "", [], "incomplete", False
        try:
            if frame.surface == "messages.create":
                unsupported = any(
                    block.type not in {"text", "tool_use"} for block in response.content
                )
                raw = "".join(block.text for block in response.content if block.type == "text")
                calls = [
                    EpisodeToolCall(call_id=b.id, tool=b.name, arguments=b.input)
                    for b in response.content
                    if b.type == "tool_use"
                ]
                finish = (
                    "tool_calls"
                    if response.stop_reason == "tool_use"
                    else (
                        "completed"
                        if response.stop_reason == "end_turn"
                        else "refused"
                        if response.stop_reason == "refusal"
                        else "incomplete"
                    )
                )
            elif frame.surface == "chat.completions.create":
                if len(response.choices) != 1:
                    raise ValueError("ONE_NATIVE_AGENT_CHOICE_REQUIRED")
                choice = response.choices[0]
                message = choice.message
                unsupported = bool(message.audio or message.function_call)
                unsupported |= any(call.type != "function" for call in message.tool_calls or [])
                raw = message.content or ""
                calls = [
                    EpisodeToolCall(
                        call_id=c.id,
                        tool=c.function.name,
                        arguments=strict_json(c.function.arguments),
                    )
                    for c in message.tool_calls or []
                ]
                finish = (
                    "refused"
                    if message.refusal
                    else (
                        "tool_calls"
                        if choice.finish_reason == "tool_calls"
                        else "completed"
                        if choice.finish_reason == "stop"
                        else "incomplete"
                    )
                )
            else:
                unsupported = any(
                    item.type not in {"message", "function_call"} for item in response.output
                )
                for item in response.output:
                    if item.type == "message":
                        unsupported |= any(
                            b.type not in {"output_text", "refusal"} for b in item.content
                        )
                        raw += "".join(b.text for b in item.content if b.type == "output_text")
                    if item.type == "function_call":
                        calls.append(
                            EpisodeToolCall(
                                call_id=item.call_id,
                                tool=item.name,
                                arguments=strict_json(item.arguments),
                            )
                        )
                refusal = any(
                    item.type == "message" and any(b.type == "refusal" for b in item.content)
                    for item in response.output
                )
                finish = "refused" if refusal else "tool_calls" if calls else "completed"
                if response.status != "completed":
                    finish = "incomplete"
            if unsupported or len(calls) > 1 or ((finish == "tool_calls") != bool(calls)):
                raise ValueError("AGENT_NATIVE_RESPONSE_PROFILE_UNSUPPORTED")
            answer = raw
            citations: tuple[EpisodeCitation, ...] = ()
            if finish == "completed":
                value = strict_json(raw)
                state, _ = output_contract(raw, EPISODE_FINAL_SCHEMA)
                if state != "passed":
                    raise ValueError("AGENT_FINAL_CONTRACT_INVALID")
                answer = value["answer"]
                citations = tuple(EpisodeCitation.model_validate(c) for c in value["citations"])
            event = EpisodeAssistant.model_validate(
                {"text": answer, "tool_calls": calls, "finish": finish}
            )
        except (ValueError, TypeError, AttributeError, RecursionError, KeyError):
            event = EpisodeAssistant(finish="incomplete")
            citations = ()
            raw = ""
        usage = LLMEvent(
            environment="experiment",
            service_name="cutback-worker",
            provider=model.provider,
            sdk_surface=frame.surface,
            requested_model=model.model,
            response_model=response.model,
            trace_id="1" * 32,
            span_id="1" * 16,
            occurred_at=datetime.now(UTC),
            duration_ms=elapsed,
            # This nonstreaming HTTP response completed and carries its usage receipt.
            # Answer validity/refusal/truncation is a separate episode outcome.
            status="success",
            metering_status="unsupported_units" if unsupported else "text_only",
            **normalize_usage(response, frame.surface),
        )
        cost = Catalog().price(usage).amount
        event = event.model_copy(
            update={
                "cost_usd": cost,
                "duration_ms": Decimal(str(elapsed)),
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
            }
        )
        return Execution(
            raw,
            cost,
            elapsed,
            usage.input_tokens,
            usage.output_tokens,
            event.finish != "incomplete",
            agent_turn=event,
            agent_citations=citations,
        )
