"""Episode contracts reject ambiguous effects and unverifiable completed traces."""

import pytest

from cutback.core.episodes import (
    EpisodeObservation,
    EpisodeScenario,
    FixtureTool,
    ToolFixture,
    bounded_episode_value,
    pointer_parts,
)


def owned_tool(**changes):
    return {
        "key": "lookup_ticket",
        "description": "Read an owned synthetic ticket.",
        "parameters": {
            "type": "object",
            "properties": {"ticket": {"type": "string"}},
            "required": ["ticket"],
            "additionalProperties": False,
        },
        "fixtures": [{"arguments": {"ticket": "synthetic-A"}, "result": {"priority": "urgent"}}],
        **changes,
    }


def observed(**changes):
    return {
        "events": [
            {"kind": "user", "text": "Read my owned synthetic ticket."},
            {
                "kind": "assistant",
                "finish": "tool_calls",
                "tool_calls": [
                    {
                        "call_id": "call_owned",
                        "tool": "lookup_ticket",
                        "arguments": {"ticket": "synthetic-A"},
                    }
                ],
            },
            {
                "kind": "tool",
                "call_id": "call_owned",
                "tool": "lookup_ticket",
                "status": "returned",
                "result": {"priority": "urgent"},
            },
            {"kind": "assistant", "finish": "completed", "text": "Urgent"},
        ],
        "outcome": "completed",
        "final_answer": "Urgent",
        **changes,
    }


def test_fixture_tools_cannot_authorize_production_io_or_implicit_side_effects():
    assert FixtureTool(**owned_tool()).effect == "read_only"
    for field in ("url", "command", "python", "headers", "credentials"):
        with pytest.raises(ValueError):
            FixtureTool(**owned_tool(**{field: "unapproved"}))
    with pytest.raises(ValueError, match="READ_ONLY_FIXTURE"):
        FixtureTool(
            **owned_tool(
                fixtures=[
                    {"arguments": {}, "result": None, "state_updates": {"/priority": "urgent"}}
                ]
            )
        )
    with pytest.raises(ValueError, match="EFFECT_ID"):
        FixtureTool(**owned_tool(effect="synthetic_state_change"))
    accepted = FixtureTool(
        **owned_tool(
            effect="synthetic_state_change",
            fixtures=[
                {
                    "arguments": {},
                    "result": None,
                    "state_updates": {"/priority": "urgent"},
                    "effect_id": "owned-priority-change",
                }
            ],
        )
    )
    assert accepted.fixtures[0].effect_id == "owned-priority-change"
    with pytest.raises(ValueError, match="DUPLICATE_TOOL_FIXTURE"):
        FixtureTool(**owned_tool(fixtures=owned_tool()["fixtures"] * 2))


def test_scenario_keeps_expected_outcome_separate_and_limits_references_and_work():
    scenario = EpisodeScenario(
        tools=[owned_tool()],
        assertions={
            "required_tools": ["lookup_ticket"],
            "accepted_answers": ["owned-evaluation-only-gold"],
        },
    )
    assert scenario.assertions.accepted_answers and scenario.execution == "synthetic_fixtures_only"
    with pytest.raises(ValueError, match="UNDECLARED_TOOL"):
        EpisodeScenario(assertions={"required_tools": ["undeclared"]})
    with pytest.raises(ValueError):
        EpisodeScenario(max_turns=True)
    with pytest.raises(ValueError):
        EpisodeScenario(relevance={"owned": True})
    with pytest.raises(ValueError):
        EpisodeScenario(execution="production_tools")
    with pytest.raises(ValueError, match="STRUCTURE_BOUND"):
        value = "owned"
        for _ in range(18):
            value = [value]
        bounded_episode_value(value)
    with pytest.raises(ValueError, match="BYTE_BOUND"):
        bounded_episode_value("x" * 100, max_bytes=10)


def test_state_pointers_have_no_implicit_root_array_append_or_prototype_operations():
    assert pointer_parts("/ticket/priority") == ["ticket", "priority"]
    assert pointer_parts("/owned~1key/~0value") == ["owned/key", "~value"]
    for value in ("", "priority", "/__proto__/key", "/a~2b", "/", "/constructor", "/x/"):
        with pytest.raises(ValueError):
            pointer_parts(value)
    with pytest.raises(ValueError):
        ToolFixture(arguments={}, result=None, state_updates={"/prototype": "denied"})


def test_completed_observation_requires_paired_native_calls_and_exact_final_answer():
    episode = EpisodeObservation(**observed())
    assert episode.events[1].cost_usd is None
    assert episode.events[1].input_tokens is None
    with pytest.raises(ValueError, match="EXACT_FINAL"):
        EpisodeObservation(**observed(final_answer="invented answer"))
    with pytest.raises(ValueError, match="EXACT_FINAL"):
        EpisodeObservation(**observed(truncated=True))
    for field, value in (("call_id", "other"), ("tool", "other")):
        body = observed()
        body["events"][2][field] = value
        with pytest.raises(ValueError, match="UNPAIRED"):
            EpisodeObservation(**body)
    body = observed()
    body["events"].pop(2)
    with pytest.raises(ValueError, match="UNRESOLVED"):
        EpisodeObservation(**body)
    body = observed()
    body["events"].insert(3, body["events"][1])
    with pytest.raises(ValueError, match="REUSED"):
        EpisodeObservation(**body)
    body = observed()
    body["events"].insert(2, {"kind": "retrieval", "call_id": "call_owned", "documents": []})
    with pytest.raises(ValueError, match="RETURNED_TOOL"):
        EpisodeObservation(**body)


def test_interrupted_episode_preserves_unresolved_call_without_inventing_success():
    body = observed(outcome="outage", final_answer=None)
    body["events"] = body["events"][:2]
    assert EpisodeObservation(**body).outcome == "outage"
