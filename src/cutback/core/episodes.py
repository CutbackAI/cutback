"""Typed encrypted episode content. Never imported by the offline source scanner.

Fixtures operate only on declared synthetic state. Assertions and relevance labels
belong to evaluation; a native executor must not include them in model messages.
"""

import json
import math
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, JsonValue, StrictBool, StrictInt, model_validator

from cutback.core.models import Identifier, StrictModel


def bounded_episode_value(value, *, max_bytes=256000):
    pending, nodes = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > 10000 or depth > 16:
            raise ValueError("EPISODE_CONTENT_STRUCTURE_BOUND")
        if isinstance(item, dict):
            if len(item) > 1000 or any(type(key) is not str for key in item):
                raise ValueError("EPISODE_CONTENT_OBJECT_BOUND")
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            if len(item) > 1000:
                raise ValueError("EPISODE_CONTENT_ARRAY_BOUND")
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("EPISODE_CONTENT_NONFINITE_NUMBER")
        elif item is not None and type(item) not in {str, int, float, bool}:
            raise ValueError("EPISODE_CONTENT_JSON_ONLY")
    try:
        if len(json.dumps(value, ensure_ascii=True, allow_nan=False).encode()) > max_bytes:
            raise ValueError("EPISODE_CONTENT_BYTE_BOUND")
    except (RecursionError, UnicodeError, OverflowError):
        raise ValueError("EPISODE_CONTENT_ENCODING_BOUND") from None
    return value


def pointer_parts(value):
    if not isinstance(value, str) or not value.startswith("/") or len(value) > 200:
        raise ValueError("EPISODE_STATE_POINTER_REQUIRED")
    parts = []
    for token in value[1:].split("/"):
        index = 0
        while index < len(token):
            if token[index] == "~":
                if index + 1 == len(token) or token[index + 1] not in "01":
                    raise ValueError("EPISODE_STATE_POINTER_ESCAPE_INVALID")
                index += 1
            index += 1
        part = token.replace("~1", "/").replace("~0", "~")
        if not part or part in {"__proto__", "constructor", "prototype"}:
            raise ValueError("EPISODE_STATE_POINTER_SEGMENT_DENIED")
        parts.append(part)
    if len(parts) > 16:
        raise ValueError("EPISODE_STATE_POINTER_DEPTH_BOUND")
    return parts


class ToolFixture(StrictModel):
    arguments: dict[str, JsonValue]
    result: JsonValue
    state_before: dict[str, JsonValue] = Field(default_factory=dict, max_length=100)
    state_updates: dict[str, JsonValue] = Field(default_factory=dict, max_length=100)
    effect_id: Identifier | None = None

    @model_validator(mode="after")
    def bounded(self):
        for key in self.state_before.keys() | self.state_updates.keys():
            pointer_parts(key)
        bounded_episode_value(self.model_dump(mode="json"), max_bytes=64000)
        return self


class FixtureTool(StrictModel):
    key: Annotated[str, Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")]
    description: str = Field(min_length=1, max_length=2000)
    parameters: dict[str, JsonValue]
    kind: Literal["tool", "retrieval"] = "tool"
    effect: Literal["read_only", "synthetic_state_change"] = "read_only"
    fixtures: list[ToolFixture] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def fixture_authority(self):
        bounded_episode_value(self.parameters, max_bytes=16000)
        if self.parameters.get("type") != "object":
            raise ValueError("EPISODE_TOOL_OBJECT_ARGUMENT_SCHEMA_REQUIRED")
        identities = []
        for fixture in self.fixtures:
            if self.kind == "retrieval":
                if self.effect != "read_only":
                    raise ValueError("RETRIEVAL_FIXTURE_MUST_BE_READ_ONLY")
                RetrievalContent.model_validate(fixture.result)
            if self.effect == "read_only" and (fixture.state_updates or fixture.effect_id):
                raise ValueError("READ_ONLY_FIXTURE_CANNOT_CHANGE_STATE")
            if self.effect == "synthetic_state_change" and (
                not fixture.state_updates or not fixture.effect_id
            ):
                raise ValueError("SYNTHETIC_STATE_CHANGE_REQUIRES_UPDATES_AND_EFFECT_ID")
            identities.append(json.dumps([fixture.arguments, fixture.state_before], sort_keys=True))
        if len(identities) != len(set(identities)):
            raise ValueError("DUPLICATE_TOOL_FIXTURE_MATCH")
        return self


class ToolArgumentCheck(StrictModel):
    tool: Identifier
    arguments: dict[str, JsonValue]
    minimum_occurrences: Annotated[StrictInt, Field(ge=1, le=100)] = 1


class ConversationCheck(StrictModel):
    user_turn: Annotated[StrictInt, Field(ge=1, le=17)]
    accepted_answers: list[Annotated[str, Field(min_length=1, max_length=16000)]] = Field(
        min_length=1, max_length=20
    )


class EpisodeAssertions(StrictModel):
    required_tools: list[Identifier] = Field(default_factory=list, max_length=32)
    forbidden_tools: list[Identifier] = Field(default_factory=list, max_length=32)
    allowed_paths: list[list[Identifier]] = Field(default_factory=list, max_length=20)
    arguments: list[ToolArgumentCheck] = Field(default_factory=list, max_length=100)
    final_state: dict[str, JsonValue] = Field(default_factory=dict, max_length=100)
    accepted_answers: list[Annotated[str, Field(min_length=1, max_length=16000)]] = Field(
        default_factory=list, max_length=100
    )
    forbid_duplicate_effects: StrictBool = True
    conversation: list[ConversationCheck] = Field(default_factory=list, max_length=17)
    accepted_outcomes: list[Literal["completed", "refused"]] = Field(
        default=["completed"], min_length=1, max_length=2
    )
    min_retrieval_precision: Decimal | None = Field(default=None, ge=0, le=1)
    min_retrieval_recall: Decimal | None = Field(default=None, ge=0, le=1)
    min_ndcg: Decimal | None = Field(default=None, ge=0, le=1)
    min_citation_support: Decimal | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def bounded(self):
        if any(len(path) > 32 for path in self.allowed_paths):
            raise ValueError("EPISODE_TOOL_PATH_BOUND")
        if set(self.required_tools) & set(self.forbidden_tools):
            raise ValueError("EPISODE_TOOL_ASSERTIONS_CONFLICT")
        if len({c.user_turn for c in self.conversation}) != len(self.conversation):
            raise ValueError("EPISODE_CONVERSATION_TURN_CHECK_MUST_BE_UNIQUE")
        for pointer in self.final_state:
            pointer_parts(pointer)
        bounded_episode_value(self.model_dump(mode="json"))
        return self


class EpisodeScenario(StrictModel):
    schema_version: Literal["cutback.episode.v1"] = "cutback.episode.v1"
    execution: Literal["synthetic_fixtures_only"] = "synthetic_fixtures_only"
    system: str = Field(default="", max_length=16000)
    user_followups: list[Annotated[str, Field(min_length=1, max_length=16000)]] = Field(
        default_factory=list, max_length=16
    )
    initial_state: dict[str, JsonValue] = Field(default_factory=dict)
    tools: list[FixtureTool] = Field(default_factory=list, max_length=16)
    assertions: EpisodeAssertions = Field(default_factory=EpisodeAssertions)
    relevance: dict[str, Annotated[StrictInt, Field(ge=0, le=10)]] = Field(
        default_factory=dict, max_length=1000
    )
    max_turns: Annotated[StrictInt, Field(ge=1, le=32)] = 8
    max_tool_calls: Annotated[StrictInt, Field(ge=0, le=100)] = 16

    @model_validator(mode="after")
    def authority(self):
        keys = [tool.key for tool in self.tools]
        if len(keys) != len(set(keys)):
            raise ValueError("EPISODE_TOOL_KEYS_MUST_BE_UNIQUE")
        needed = set(self.assertions.required_tools)
        needed.update(check.tool for check in self.assertions.arguments)
        for path in self.assertions.allowed_paths:
            needed.update(path)
        if not needed <= set(keys):
            raise ValueError("EPISODE_ASSERTION_REFERENCES_UNDECLARED_TOOL")
        if any(c.user_turn > 1 + len(self.user_followups) for c in self.assertions.conversation):
            raise ValueError("EPISODE_CONVERSATION_CHECK_REQUIRES_DECLARED_USER_TURN")
        if any(not isinstance(key, str) or not 1 <= len(key) <= 200 for key in self.relevance):
            raise ValueError("EPISODE_DOCUMENT_ID_BOUND")
        bounded_episode_value(self.model_dump(mode="json"))
        return self


class EpisodeToolCall(StrictModel):
    call_id: Identifier
    tool: Identifier
    arguments: dict[str, JsonValue]

    @model_validator(mode="after")
    def bounded(self):
        bounded_episode_value(self.arguments, max_bytes=64000)
        return self


class EpisodeUser(StrictModel):
    kind: Literal["user"] = "user"
    text: str = Field(min_length=1, max_length=64000)


class EpisodeAssistant(StrictModel):
    kind: Literal["assistant"] = "assistant"
    text: str = Field(default="", max_length=64000)
    tool_calls: list[EpisodeToolCall] = Field(default_factory=list, max_length=16)
    finish: Literal["completed", "tool_calls", "refused", "incomplete"]
    cost_usd: Decimal | None = Field(default=None, ge=0)
    duration_ms: Decimal | None = Field(default=None, ge=0)
    input_tokens: Annotated[StrictInt | None, Field(ge=0)] = None
    output_tokens: Annotated[StrictInt | None, Field(ge=0)] = None

    @model_validator(mode="after")
    def shape(self):
        if (self.finish == "tool_calls") != bool(self.tool_calls):
            raise ValueError("EPISODE_TOOL_CALL_FINISH_MISMATCH")
        return self


class EpisodeToolResult(StrictModel):
    kind: Literal["tool"] = "tool"
    call_id: Identifier
    tool: Identifier
    status: Literal["returned", "denied", "fixture_error", "uncertain"]
    result: JsonValue = None
    effect_id: Identifier | None = None
    duplicate_effect_attempt: StrictBool = False


class RetrievedDocument(StrictModel):
    document_id: str = Field(min_length=1, max_length=200)
    # Backend relevance scores are distinct from independently declared reference grades.
    backend_score: Decimal | None = None


class RetrievalChunk(RetrievedDocument):
    text: str = Field(max_length=64000)


class RetrievalContent(StrictModel):
    documents: list[RetrievalChunk] = Field(max_length=1000)

    @model_validator(mode="after")
    def unique(self):
        if len({d.document_id for d in self.documents}) != len(self.documents):
            raise ValueError("EPISODE_RETRIEVAL_DUPLICATE_DOCUMENT_IDS")
        return self


class EpisodeRetrieval(StrictModel):
    kind: Literal["retrieval"] = "retrieval"
    call_id: Identifier
    documents: list[RetrievedDocument] = Field(max_length=1000)

    @model_validator(mode="after")
    def unique(self):
        if len({d.document_id for d in self.documents}) != len(self.documents):
            raise ValueError("EPISODE_RETRIEVAL_DUPLICATE_DOCUMENT_IDS")
        return self


EpisodeEvent = Annotated[
    EpisodeUser | EpisodeAssistant | EpisodeToolResult | EpisodeRetrieval,
    Field(discriminator="kind"),
]


class EpisodeCitation(StrictModel):
    document_id: str = Field(min_length=1, max_length=200)
    quote: str = Field(min_length=1, max_length=64000)


class EpisodeObservation(StrictModel):
    schema_version: Literal["cutback.episode-observation.v1"] = "cutback.episode-observation.v1"
    events: list[EpisodeEvent] = Field(min_length=1, max_length=512)
    outcome: Literal[
        "completed",
        "refused",
        "turn_limit",
        "tool_limit",
        "timeout",
        "cancelled",
        "outage",
        "content_limit",
        "incomplete",
        "policy_denied",
        "budget_limit",
        "unknown_usage",
        "error",
    ]
    final_answer: str | None = Field(default=None, max_length=64000)
    citations: list[EpisodeCitation] = Field(default_factory=list, max_length=1000)
    final_state: dict[str, JsonValue] = Field(default_factory=dict)
    truncated: StrictBool = False

    @model_validator(mode="after")
    def pairing(self):
        pending: dict[str, str] = {}
        seen, returned, retrievals = set(), set(), set()
        previous = None
        for event in self.events:
            if event.kind in {"user", "assistant"} and pending:
                raise ValueError("EPISODE_UNRESOLVED_TOOL_RESULTS_BEFORE_NEXT_MESSAGE")
            if event.kind == "assistant":
                if previous not in {"user", "tool", "retrieval"}:
                    raise ValueError("EPISODE_ASSISTANT_REQUIRES_NEW_INPUT")
                for call in event.tool_calls:
                    if call.call_id in seen:
                        raise ValueError("EPISODE_TOOL_CALL_ID_REUSED")
                    seen.add(call.call_id)
                    pending[call.call_id] = call.tool
            elif event.kind == "tool":
                if pending.pop(event.call_id, None) != event.tool:
                    raise ValueError("EPISODE_TOOL_RESULT_UNPAIRED_OR_WRONG_TOOL")
                if event.status == "returned":
                    returned.add(event.call_id)
            elif event.kind == "retrieval":
                if event.call_id not in returned:
                    raise ValueError("EPISODE_RETRIEVAL_REQUIRES_RETURNED_TOOL_RESULT")
                if event.call_id in retrievals:
                    raise ValueError("EPISODE_RETRIEVAL_CALL_ID_REUSED")
                retrievals.add(event.call_id)
            previous = event.kind
        if self.outcome in {"completed", "refused"} and (
            pending
            or self.truncated
            or not isinstance(self.events[-1], EpisodeAssistant)
            or self.events[-1].finish != self.outcome
            or self.final_answer != self.events[-1].text
        ):
            raise ValueError("EPISODE_COMPLETED_REQUIRES_EXACT_FINAL_ASSISTANT_AND_COMPLETE_TRACE")
        bounded_episode_value(self.model_dump(mode="json"))
        return self
