import json

import pytest
from test_episodes import owned_tool

from cutback.core.agent_turns import AgentTurnFrame


def frame(surface):
    tool = owned_tool()
    return AgentTurnFrame(
        surface=surface,
        system="Owned synthetic system instruction",
        tools=[
            {
                "key": tool["key"],
                "description": tool["description"],
                "parameters": tool["parameters"],
            }
        ],
        messages=[
            {"role": "user", "text": "Read owned ticket"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "call_id": "call_owned",
                        "tool": "lookup_ticket",
                        "arguments": {"ticket": "synthetic-A"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_owned", "text": '{"priority":"urgent"}'},
        ],
    )


@pytest.mark.parametrize(
    "surface", ["responses.create", "chat.completions.create", "messages.create"]
)
def test_agent_turn_preserves_native_pairs_and_only_allows_declared_function_tools(surface):
    request = frame(surface)
    body = request.native_payload()
    serialized = json.dumps(body)
    assert "call_owned" in serialized and "synthetic-A" in serialized and "urgent" in serialized
    assert "fixtures" not in serialized and "assertions" not in serialized
    assert "stream" not in body and "headers" not in body and "base_url" not in body
    if surface == "messages.create":
        assert body["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
        assert body["messages"][-1]["content"][0]["tool_use_id"] == "call_owned"
        body["tools"][0]["input_schema"]["properties"]["ticket"]["type"] = "number"
        assert request.tools[0].parameters["properties"]["ticket"]["type"] == "string"
    else:
        assert body["store"] is False and body["parallel_tool_calls"] is False
        if surface == "responses.create":
            assert body["input"][-1]["type"] == "function_call_output"
            assert body["input"][-1]["call_id"] == "call_owned"
        else:
            assert body["messages"][-1]["role"] == "tool"
            assert body["messages"][-1]["tool_call_id"] == "call_owned"


def test_agent_turn_rejects_missing_results_duplicate_ids_and_arbitrary_native_options():
    body = frame("responses.create").model_dump(mode="json")
    body["messages"].pop()
    with pytest.raises(ValueError, match="COMPLETED_INPUT"):
        AgentTurnFrame(**body)
    body = frame("responses.create").model_dump(mode="json")
    body["messages"][-1]["tool_call_id"] = "other"
    with pytest.raises(ValueError, match="UNPAIRED"):
        AgentTurnFrame(**body)
    for field, value in (
        ("stream", True),
        ("base_url", "https://unapproved.invalid"),
        ("tool_choice", "required"),
        ("headers", {}),
    ):
        with pytest.raises(ValueError):
            AgentTurnFrame(**{**frame("responses.create").model_dump(mode="json"), field: value})


def test_message_transport_cannot_silently_drop_an_unqualified_argument_constraint():
    body = frame("messages.create").model_dump(mode="json")
    body["tools"][0]["parameters"]["properties"]["ticket"]["minLength"] = 2
    with pytest.raises(ValueError, match="KEYWORD_NOT_QUALIFIED"):
        AgentTurnFrame(**body)
