"""Replay explicit fixtures against ephemeral state. No network, shell or real tool calls."""

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Literal

from cutback.core.episodes import (
    EpisodeRetrieval,
    EpisodeScenario,
    EpisodeToolCall,
    EpisodeToolResult,
    RetrievalContent,
    RetrievedDocument,
    bounded_episode_value,
    pointer_parts,
)
from cutback.evals.metrics import MetricSpec
from cutback.evals.output_contracts import output_contract


def same_json(first, second):
    return json.dumps(first, sort_keys=True, allow_nan=False) == json.dumps(
        second, sort_keys=True, allow_nan=False
    )


def descend(value, part):
    if isinstance(value, dict):
        return value[part]
    if isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", part):
        return value[int(part)]
    raise KeyError("EPISODE_STATE_LOCATION_UNAVAILABLE")


def state_value(state, pointer):
    value = state
    for part in pointer_parts(pointer):
        value = descend(value, part)
    return value


def write_state(state, pointer, value):
    parts = pointer_parts(pointer)
    parent = state
    for part in parts[:-1]:
        parent = descend(parent, part)
    if isinstance(parent, dict):
        parent[parts[-1]] = deepcopy(value)
    elif isinstance(parent, list) and re.fullmatch(r"0|[1-9][0-9]*", parts[-1]):
        parent[int(parts[-1])] = deepcopy(value)
    else:
        raise KeyError("EPISODE_STATE_LOCATION_UNAVAILABLE")


@dataclass
class FixtureExecution:
    event: EpisodeToolResult
    retrieval: EpisodeRetrieval | None = None
    stop: Literal["policy_denied", "outage"] | None = None


class FixtureRunner:
    def __init__(self, scenario: EpisodeScenario):
        self.scenario = scenario
        self.state = deepcopy(scenario.initial_state)
        self.effects: set[str] = set()
        self.calls: set[str] = set()
        self.tools = {tool.key: tool for tool in scenario.tools}
        for tool in scenario.tools:
            # Validate the supported schema profile without loading refs or regex code.
            MetricSpec(kind="json_schema", minimum=1, schema_definition=tool.parameters)

    def visible_tools(self):
        # Keep fixture responses, final-state targets and relevance judgments out of model input.
        return [
            {"key": t.key, "description": t.description, "parameters": deepcopy(t.parameters)}
            for t in self.scenario.tools
        ]

    def execute(self, call: EpisodeToolCall):
        if call.call_id in self.calls:
            raise ValueError("EPISODE_TOOL_CALL_ID_REUSED")
        self.calls.add(call.call_id)

        def reject(code, status="denied"):
            return FixtureExecution(
                EpisodeToolResult(
                    call_id=call.call_id, tool=call.tool, status=status, result={"code": code}
                )
            )

        if len(self.calls) > self.scenario.max_tool_calls:
            return reject("EPISODE_TOOL_CALL_LIMIT")
        tool = self.tools.get(call.tool)
        if tool is None:
            return reject("EPISODE_TOOL_NOT_APPROVED")
        state, code = output_contract(json.dumps(call.arguments), tool.parameters)
        if state != "passed":
            return reject(
                "EPISODE_TOOL_ARGUMENT_SCHEMA_DENIED"
                if state == "failed"
                else "EPISODE_TOOL_SCHEMA_WORK_LIMIT",
                "denied" if state == "failed" else "fixture_error",
            )
        matches = []
        for fixture in tool.fixtures:
            if not same_json(call.arguments, fixture.arguments):
                continue
            try:
                if all(
                    same_json(state_value(self.state, path), value)
                    for path, value in fixture.state_before.items()
                ):
                    matches.append(fixture)
            except (KeyError, IndexError):
                continue
        if len(matches) != 1:
            if not matches and any(
                same_json(call.arguments, fixture.arguments) and fixture.effect_id in self.effects
                for fixture in tool.fixtures
                if fixture.effect_id
            ):
                return FixtureExecution(
                    EpisodeToolResult(
                        call_id=call.call_id,
                        tool=call.tool,
                        status="denied",
                        result={"code": "EPISODE_DUPLICATE_EFFECT_ATTEMPT"},
                        duplicate_effect_attempt=True,
                    )
                )
            return reject(
                "EPISODE_FIXTURE_NOT_DECLARED"
                if not matches
                else "EPISODE_FIXTURE_MATCH_AMBIGUOUS",
                "denied" if not matches else "fixture_error",
            )
        fixture = matches[0]
        duplicate = bool(fixture.effect_id and fixture.effect_id in self.effects)
        if not duplicate:
            staged = deepcopy(self.state)
            try:
                for path, value in fixture.state_updates.items():
                    write_state(staged, path, value)
                bounded_episode_value(staged)
            except (KeyError, IndexError, ValueError):
                return reject("EPISODE_FIXTURE_STATE_UPDATE_INVALID", "fixture_error")
            self.state = staged
            if fixture.effect_id:
                self.effects.add(fixture.effect_id)
        event = EpisodeToolResult(
            call_id=call.call_id,
            tool=call.tool,
            status="returned",
            result=deepcopy(fixture.result),
            effect_id=fixture.effect_id,
            duplicate_effect_attempt=duplicate,
        )
        retrieval = None
        if tool.kind == "retrieval":
            content = RetrievalContent.model_validate(fixture.result)
            retrieval = EpisodeRetrieval(
                call_id=call.call_id,
                documents=[
                    RetrievedDocument(document_id=d.document_id, backend_score=d.backend_score)
                    for d in content.documents
                ],
            )
        return FixtureExecution(event, retrieval)
