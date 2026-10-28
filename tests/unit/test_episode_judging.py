"""Observed-source-only proxy judging; labels and unreturned fixtures stay out of inputs."""

import copy
import json

import pytest
from test_episode_fixtures import call
from test_episode_metrics import episode
from test_episodes import owned_tool

from cutback.core.episode_judging import EpisodeJudgingSpec
from cutback.core.episodes import EpisodeScenario
from cutback.evals.episode_judging import assess_judgment, judging_document


def evidence():
    scenario = EpisodeScenario(
        tools=[
            owned_tool(
                kind="retrieval",
                fixtures=[
                    {
                        "arguments": {"ticket": "synthetic-A"},
                        "result": {
                            "documents": [
                                {
                                    "document_id": "owned",
                                    "text": "A return is permitted for 30 days.",
                                }
                            ]
                        },
                    },
                    {
                        "arguments": {"ticket": "synthetic-unreturned"},
                        "result": {
                            "documents": [
                                {
                                    "document_id": "unseen",
                                    "text": "UNRETURNED_FIXTURE_MUST_NOT_LEAVE",
                                }
                            ]
                        },
                    },
                ],
            )
        ],
        assertions={"accepted_answers": ["Returns last 30 days."]},
    )
    observed = episode(
        scenario, [call("lookup_ticket", ticket="synthetic-A")], answer="Returns last 30 days."
    )
    spec = EpisodeJudgingSpec(
        criteria=[
            {
                "key": "grounded",
                "kind": "retrieval_groundedness",
                "judge_rubric": "The answer must be supported by retrieved source facts.",
                "minimum": 1.0,
            }
        ]
    )
    return spec, scenario, observed


def test_proxy_input_uses_actual_returned_documents_and_keeps_unrequested_labels_private():
    spec, scenario, observed = evidence()
    text, sources, allowed = judging_document(
        spec, scenario, observed, {"gold": "UNREQUESTED_GOLD"}
    )
    assert "UNRETURNED_FIXTURE" not in text and "UNREQUESTED_GOLD" not in text
    assert sources == {"retrieved:0": "A return is permitted for 30 days."}
    assert allowed == {"grounded": ["retrieved:0"]}
    assert json.loads(text)["final_answer"] == "Returns last 30 days."
    report = assess_judgment(
        spec,
        json.dumps(
            {
                "checks": [
                    {
                        "key": "grounded",
                        "score": 1,
                        "citations": [{"source_id": "retrieved:0", "quote": "30 days"}],
                    }
                ]
            }
        ),
        sources,
        allowed,
    )
    assert report["checks"][0]["status"] == "passed" and report["evidence_tier"] == 3
    assert report["semantic_quality"] == "NOT VERIFIED"
    assert "30 days" not in json.dumps(report)


@pytest.mark.parametrize(
    "damage",
    ["unknown_source", "false_quote", "blank_quote", "duplicate", "extra_key", "bool_score"],
)
def test_invalid_judge_evidence_never_becomes_a_proxy_pass(damage):
    spec, scenario, observed = evidence()
    _, sources, allowed = judging_document(spec, scenario, observed, {})
    vote = {
        "key": "grounded",
        "score": 1,
        "citations": [{"source_id": "retrieved:0", "quote": "30 days"}],
    }
    if damage == "unknown_source":
        vote["citations"][0]["source_id"] = "reference:0"
    elif damage == "false_quote":
        vote["citations"][0]["quote"] = "500 days"
    elif damage == "blank_quote":
        vote["citations"][0]["quote"] = " "
    elif damage == "duplicate":
        vote["citations"] *= 2
    elif damage == "extra_key":
        vote["extra"] = "untrusted"
    else:
        vote["score"] = True
    with pytest.raises(ValueError, match="EPISODE_JUDGE"):
        assess_judgment(spec, json.dumps({"checks": [vote]}), sources, allowed)


def test_forged_tool_observation_and_unavailable_answer_stop_before_judging():
    spec, scenario, observed = evidence()
    tampered = copy.deepcopy(observed)
    tampered.events[2].result["documents"][0]["text"] = "invented"
    with pytest.raises(ValueError, match="OBSERVATION_INTEGRITY"):
        judging_document(spec, scenario, tampered, {})
    observed.truncated = True
    with pytest.raises(ValueError, match="COMPLETE_ANSWER"):
        judging_document(spec, scenario, observed, {})


def test_correctness_references_are_explicit_and_do_not_become_customer_verification():
    spec, scenario, observed = evidence()
    spec.criteria[0].kind = "answer_correctness"
    text, sources, allowed = judging_document(spec, scenario, observed, {})
    assert "retrieved:0" not in sources and "reference:0" in allowed["grounded"]
    assert "Returns last 30 days." in text
    scenario.assertions.accepted_answers = []
    with pytest.raises(ValueError, match="CORRECTNESS_REFERENCE_REQUIRED"):
        judging_document(spec, scenario, observed, {})
