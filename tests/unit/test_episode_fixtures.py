"""Actual synthetic state transitions, fail-closed fixtures and non-leaking tool projections."""

import json

import pytest
from test_episodes import owned_tool

from cutback.core.episodes import EpisodeScenario, EpisodeToolCall
from cutback.evals.episode_fixtures import FixtureRunner, state_value, write_state


def call(name="update_ticket", identifier="call_owned", **arguments):
    return EpisodeToolCall(call_id=identifier, tool=name, arguments=arguments)


def mutable_tool(**changes):
    return owned_tool(
        key="update_ticket",
        effect="synthetic_state_change",
        fixtures=[
            {
                "arguments": {"ticket": "synthetic-A"},
                "result": {"status": "changed"},
                "state_updates": {"/ticket/priority": "urgent"},
                "effect_id": "priority-change",
            }
        ],
        **changes,
    )


def test_effect_is_applied_once_and_returned_objects_do_not_alias_fixture_or_state():
    scenario = EpisodeScenario(
        initial_state={"ticket": {"priority": "normal"}}, tools=[mutable_tool()]
    )
    runner = FixtureRunner(scenario)
    first = runner.execute(call(ticket="synthetic-A"))
    assert first.event.status == "returned" and not first.event.duplicate_effect_attempt
    assert runner.state["ticket"]["priority"] == "urgent"
    first.event.result["status"] = "tampered"
    assert scenario.tools[0].fixtures[0].result == {"status": "changed"}
    second = runner.execute(call(identifier="call_other", ticket="synthetic-A"))
    assert second.event.status == "returned" and second.event.duplicate_effect_attempt
    assert runner.state["ticket"]["priority"] == "urgent" and len(runner.effects) == 1
    with pytest.raises(ValueError, match="ID_REUSED"):
        runner.execute(call(ticket="synthetic-A"))


def test_denied_tools_arguments_and_limits_have_no_effect():
    runner = FixtureRunner(
        EpisodeScenario(
            initial_state={"ticket": {"priority": "normal"}},
            tools=[mutable_tool()],
            max_tool_calls=3,
        )
    )
    assert (
        runner.execute(call(name="send_http", url="https://unapproved.invalid")).event.status
        == "denied"
    )
    assert runner.execute(call(identifier="bad_schema", ticket=1)).event.status == "denied"
    assert (
        runner.execute(call(identifier="not_frozen", ticket="not-approved")).event.status
        == "denied"
    )
    result = runner.execute(call(identifier="past_cap", ticket="synthetic-A"))
    assert result.event.result == {"code": "EPISODE_TOOL_CALL_LIMIT"}
    assert runner.state == {"ticket": {"priority": "normal"}} and not runner.effects


def test_ambiguous_preconditions_and_invalid_later_update_cannot_partially_apply():
    tool = mutable_tool()
    tool["fixtures"][0]["state_updates"] = {"/ticket/priority": "urgent", "/missing/leaf": "denied"}
    runner = FixtureRunner(
        EpisodeScenario(initial_state={"ticket": {"priority": "normal"}}, tools=[tool])
    )
    result = runner.execute(call(ticket="synthetic-A"))
    assert result.event.status == "fixture_error"
    assert runner.state == {"ticket": {"priority": "normal"}} and not runner.effects
    tool = mutable_tool()
    tool["fixtures"].append({**tool["fixtures"][0], "state_before": {"/ticket/priority": "normal"}})
    runner = FixtureRunner(
        EpisodeScenario(initial_state={"ticket": {"priority": "normal"}}, tools=[tool])
    )
    assert runner.execute(call(ticket="synthetic-A")).event.result == {
        "code": "EPISODE_FIXTURE_MATCH_AMBIGUOUS"
    }
    assert runner.state["ticket"]["priority"] == "normal"


def test_retrieval_ids_come_from_returned_documents_and_reference_gold_is_not_in_visible_tools():
    tool = owned_tool(
        kind="retrieval",
        fixtures=[
            {
                "arguments": {"ticket": "synthetic-A"},
                "result": {
                    "documents": [{"document_id": "owned-doc", "text": "Owned retrieved content"}]
                },
            }
        ],
    )
    scenario = EpisodeScenario(
        tools=[tool],
        relevance={"owned-doc": 3},
        assertions={"accepted_answers": ["evaluation-only-canary"]},
    )
    runner = FixtureRunner(scenario)
    visible = json.dumps(runner.visible_tools())
    assert all(
        value not in visible
        for value in ("evaluation-only-canary", "relevance", "Owned retrieved content", "fixtures")
    )
    result = runner.execute(call(name="lookup_ticket", ticket="synthetic-A"))
    assert result.retrieval.documents[0].document_id == "owned-doc"
    assert result.retrieval.documents[0].backend_score is None
    assert result.event.result["documents"][0]["text"] == "Owned retrieved content"
    with pytest.raises(ValueError):
        FixtureRunner(
            EpisodeScenario(tools=[owned_tool(parameters={"$ref": "https://not-approved.invalid"})])
        )


def test_state_pointer_never_appends_or_evaluates_a_path():
    state = {"owned": ["original"]}
    write_state(state, "/owned/0", "changed")
    assert state_value(state, "/owned/0") == "changed"
    for path in ("/owned/-", "/owned/-1", "/owned/1", "/owned/0:1"):
        with pytest.raises((KeyError, IndexError)):
            write_state(state, path, "denied")
    assert state == {"owned": ["changed"]}
