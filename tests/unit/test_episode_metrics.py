from copy import deepcopy
from decimal import Decimal

import pytest
from test_episode_fixtures import call, mutable_tool
from test_episodes import owned_tool

from cutback.core.episodes import EpisodeObservation, EpisodeScenario
from cutback.evals.episode_fixtures import FixtureRunner
from cutback.evals.episode_metrics import assess_episode, retrieval_scores


def episode(scenario, calls, *, answer="owned answer", citations=None):
    runner = FixtureRunner(scenario)
    events = [{"kind": "user", "text": "Owned synthetic task"}]
    for action in calls:
        events.append(
            {
                "kind": "assistant",
                "finish": "tool_calls",
                "tool_calls": [action.model_dump(mode="json")],
            }
        )
        result = runner.execute(action)
        events.append(result.event.model_dump(mode="json"))
        if result.retrieval:
            events.append(result.retrieval.model_dump(mode="json"))
    events.append({"kind": "assistant", "finish": "completed", "text": answer})
    return EpisodeObservation(
        events=events,
        outcome="completed",
        final_answer=answer,
        final_state=runner.state,
        citations=citations or [],
    )


def test_alternative_paths_require_actual_final_state_and_duplicate_attempts_fail():
    scenario = EpisodeScenario(
        initial_state={"ticket": {"priority": "normal"}},
        tools=[owned_tool(), mutable_tool()],
        assertions={
            "allowed_paths": [["update_ticket"], ["lookup_ticket", "update_ticket"]],
            "final_state": {"/ticket/priority": "urgent"},
            "arguments": [{"tool": "update_ticket", "arguments": {"ticket": "synthetic-A"}}],
        },
    )
    direct = episode(scenario, [call(ticket="synthetic-A")])
    indirect = episode(
        scenario, [call("lookup_ticket", "read", ticket="synthetic-A"), call(ticket="synthetic-A")]
    )
    assert assess_episode(scenario, direct)["status"] == "passed"
    assert assess_episode(scenario, indirect)["status"] == "passed"
    repeated = episode(
        scenario, [call(ticket="synthetic-A"), call(identifier="again", ticket="synthetic-A")]
    )
    report = assess_episode(scenario, repeated)
    assert report["status"] == "failed" and report["score"] == 0
    tampered = deepcopy(direct)
    tampered.final_state["ticket"]["priority"] = "invented"
    assert assess_episode(scenario, tampered)["status"] == "error"
    assert "priority" not in str(assess_episode(scenario, direct))


def test_retrieval_missing_judgments_and_empty_lists_do_not_manufacture_precision():
    empty = retrieval_scores([], {"relevant": 1})
    assert empty["precision"] is None and empty["recall"] == 0 and empty["ndcg"] == 0
    unjudged = retrieval_scores(["unknown", "relevant"], {"relevant": 1})
    assert unjudged["precision"] is None and unjudged["ndcg"] is None
    assert unjudged["recall"] == 1 and unjudged["unjudged"] == 1
    report = retrieval_scores(["irrelevant", "relevant"], {"relevant": 1, "irrelevant": 0})
    assert report["precision"] == Decimal("0.5") and report["recall"] == 1
    assert 0 < report["ndcg"] < 1
    with pytest.raises(ValueError):
        retrieval_scores(["relevant", "relevant"], {"relevant": 1})


def test_citation_quotes_need_the_actual_returned_document_and_never_verify_semantics():
    tool = owned_tool(
        kind="retrieval",
        fixtures=[
            {
                "arguments": {"ticket": "synthetic-A"},
                "result": {
                    "documents": [
                        {"document_id": "owned-doc", "text": "The owned answer is urgent."}
                    ]
                },
            }
        ],
    )
    scenario = EpisodeScenario(
        tools=[tool],
        relevance={"owned-doc": 1},
        assertions={
            "min_retrieval_precision": "1",
            "min_retrieval_recall": "1",
            "min_citation_support": "1",
        },
    )
    observed = episode(
        scenario,
        [call("lookup_ticket", ticket="synthetic-A")],
        citations=[{"document_id": "owned-doc", "quote": "answer is urgent"}],
    )
    report = assess_episode(scenario, observed)
    assert report["status"] == "passed" and report["citation_semantics"] == "not_verified"
    observed.citations[0].quote = "invented quote"
    assert assess_episode(scenario, observed)["status"] == "failed"
    observed.events[3].documents[0].document_id = "invented-document"
    assert assess_episode(scenario, observed)["status"] == "error"


def test_vendor_outage_is_unknown_without_misclassifying_unobserved_required_tools():
    scenario = EpisodeScenario(
        tools=[owned_tool()],
        assertions={
            "required_tools": ["lookup_ticket"],
            "forbidden_tools": ["send_http"],
            "accepted_answers": ["owned answer"],
        },
    )
    observed = EpisodeObservation(
        events=[{"kind": "user", "text": "Owned synthetic question"}],
        outcome="outage",
        final_state={},
    )
    report = assess_episode(scenario, observed)
    assert report["status"] == "unknown" and report["score"] is None
    assert all(c["status"] == "unknown" for c in report["checks"])


def test_replayed_json_boolean_cannot_impersonate_a_numeric_tool_result():
    tool = owned_tool(fixtures=[{"arguments": {"ticket": "synthetic-A"}, "result": {"count": 1}}])
    scenario = EpisodeScenario(tools=[tool])
    observed = episode(scenario, [call("lookup_ticket", ticket="synthetic-A")])
    observed.events[2].result["count"] = True
    assert assess_episode(scenario, observed)["status"] == "error"
