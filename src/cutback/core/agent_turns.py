"""Model-visible agent turns. No fixture answers, reference labels or arbitrary SDK options."""

import json
from copy import deepcopy
from typing import Any, Literal

from pydantic import Field, JsonValue, StrictBool, model_validator

from cutback.core.episodes import EpisodeToolCall, bounded_episode_value
from cutback.core.models import StrictModel
from cutback.core.prompts import validate_native_schema

EPISODE_FINAL_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"document_id": {"type": "string"}, "quote": {"type": "string"}},
                "required": ["document_id", "quote"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["answer", "citations"],
    "additionalProperties": False,
}


class AgentToolDefinition(StrictModel):
    key: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    description: str = Field(min_length=1, max_length=2000)
    parameters: dict[str, JsonValue]


class AgentMessage(StrictModel):
    role: Literal["user", "assistant", "tool"]
    text: str = Field(default="", max_length=64000)
    tool_calls: list[EpisodeToolCall] = Field(default_factory=list, max_length=1)
    tool_call_id: str | None = Field(default=None, min_length=1, max_length=200)
    tool_error: StrictBool = False

    @model_validator(mode="after")
    def shape(self):
        if (self.role == "tool") != (self.tool_call_id is not None):
            raise ValueError("AGENT_TOOL_MESSAGE_REQUIRES_EXACT_CALL_ID")
        if self.tool_calls and self.role != "assistant" or self.tool_error and self.role != "tool":
            raise ValueError("AGENT_MESSAGE_ROLE_FIELD_MISMATCH")
        return self


class AgentTurnFrame(StrictModel):
    schema_version: Literal["cutback.agent-turn.v1"] = "cutback.agent-turn.v1"
    surface: Literal["responses.create", "chat.completions.create", "messages.create"]
    system: str = Field(default="", max_length=16000)
    messages: list[AgentMessage] = Field(min_length=1, max_length=100)
    tools: list[AgentToolDefinition] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def qualified(self):
        keys = {tool.key for tool in self.tools}
        if len(keys) != len(self.tools):
            raise ValueError("AGENT_TOOL_KEYS_MUST_BE_UNIQUE")
        for tool in self.tools:
            validate_native_schema(tool.parameters, self.surface)
        pending: set[str] = set()
        used: set[str] = set()
        previous = None
        for message in self.messages:
            if message.role != "tool" and pending:
                raise ValueError("AGENT_PENDING_TOOL_RESULT_REQUIRED")
            if message.role == "assistant":
                if previous not in {"user", "tool"}:
                    raise ValueError("AGENT_ASSISTANT_REQUIRES_NEW_INPUT")
                for call in message.tool_calls:
                    if call.call_id in used:
                        raise ValueError("AGENT_TOOL_CALL_ID_REUSED")
                    used.add(call.call_id)
                    pending.add(call.call_id)
            if message.role == "tool":
                if message.tool_call_id not in pending:
                    raise ValueError("AGENT_TOOL_MESSAGE_UNPAIRED")
                pending.remove(message.tool_call_id)
            previous = message.role
        if pending or previous == "assistant":
            raise ValueError("AGENT_NEXT_TURN_REQUIRES_COMPLETED_INPUT")
        bounded_episode_value(self.native_payload(), max_bytes=128000)
        return self

    def native_payload(self):
        if self.surface == "messages.create":
            return self._anthropic()
        return self._openai()

    def _openai(self):
        messages: list[dict[str, Any]] = []
        if self.system:
            messages.append({"role": "system", "content": self.system})
        responses = self.surface == "responses.create"
        for message in self.messages:
            if message.role == "tool":
                assert message.tool_call_id is not None
                messages.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.tool_call_id,
                        "output": message.text,
                    }
                    if responses
                    else {
                        "role": "tool",
                        "tool_call_id": message.tool_call_id,
                        "content": message.text,
                    }
                )
            elif responses:
                if message.text or not message.tool_calls:
                    messages.append({"role": message.role, "content": message.text})
                for call in message.tool_calls:
                    messages.append(
                        {
                            "type": "function_call",
                            "call_id": call.call_id,
                            "name": call.tool,
                            "arguments": json.dumps(call.arguments),
                        }
                    )
            else:
                value: dict[str, Any] = {"role": message.role, "content": message.text}
                if message.tool_calls:
                    value["tool_calls"] = [
                        {
                            "id": call.call_id,
                            "type": "function",
                            "function": {
                                "name": call.tool,
                                "arguments": json.dumps(call.arguments),
                            },
                        }
                        for call in message.tool_calls
                    ]
                messages.append(value)
        options: dict[str, Any] = {"store": False, "input" if responses else "messages": messages}
        schema = {
            "name": "cutback_episode_final",
            "strict": True,
            "schema": deepcopy(EPISODE_FINAL_SCHEMA),
        }
        if responses:
            options["text"] = {"format": {"type": "json_schema", **schema}}
        else:
            options["response_format"] = {"type": "json_schema", "json_schema": schema}
        if self.tools:
            definitions = [
                {
                    "name": tool.key,
                    "description": tool.description,
                    "parameters": deepcopy(tool.parameters),
                    "strict": True,
                }
                for tool in self.tools
            ]
            options["tools"] = [
                {"type": "function", **tool}
                if responses
                else {"type": "function", "function": tool}
                for tool in definitions
            ]
            options["tool_choice"], options["parallel_tool_calls"] = "auto", False
        return options

    def _anthropic(self):
        messages: list[dict[str, Any]] = []
        for message in self.messages:
            if message.role == "tool":
                role = "user"
                content: list[dict[str, Any]] = [
                    {
                        "type": "tool_result",
                        "tool_use_id": message.tool_call_id,
                        "content": message.text,
                        "is_error": message.tool_error,
                    }
                ]
            else:
                role = message.role
                content = [{"type": "text", "text": message.text}] if message.text else []
                content.extend(
                    {
                        "type": "tool_use",
                        "id": call.call_id,
                        "name": call.tool,
                        "input": deepcopy(call.arguments),
                    }
                    for call in message.tool_calls
                )
            if not content:
                raise ValueError("ANTHROPIC_AGENT_MESSAGE_CANNOT_BE_EMPTY")
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"].extend(content)
            else:
                messages.append({"role": role, "content": content})
        payload: dict[str, Any] = {
            "messages": messages,
            "output_config": {
                "format": {"type": "json_schema", "schema": deepcopy(EPISODE_FINAL_SCHEMA)}
            },
        }
        if self.system:
            payload["system"] = [{"type": "text", "text": self.system}]
        if self.tools:
            payload["tools"] = [
                {
                    "name": tool.key,
                    "description": tool.description,
                    "input_schema": deepcopy(tool.parameters),
                }
                for tool in self.tools
            ]
            payload["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
        return payload
