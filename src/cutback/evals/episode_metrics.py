"""Replay declared synthetic evidence, then score independent typed constraints.

Literal citation support is not semantic correctness. Costs in imported observations
remain assertions; the durable worker ledger is the accounting authority.
"""

import math
from decimal import Decimal

from cutback.core.episodes import EpisodeObservation, EpisodeScenario, RetrievalContent
from cutback.evals.episode_fixtures import FixtureRunner, same_json, state_value


def retrieval_scores(documents, relevance, cutoff=10):
    if type(cutoff) is not int or not 1 <= cutoff <= 1000:
        raise ValueError("EPISODE_RETRIEVAL_CUTOFF_BOUND")
    if (
        len(documents) > 1000
        or len(set(documents)) != len(documents)
        or len(relevance) > 1000
        or any(type(grade) is not int or not 0 <= grade <= 10 for grade in relevance.values())
    ):
        raise ValueError("EPISODE_RETRIEVAL_REFERENCE_BOUND")
    selected = documents[:cutoff]
    positive = {key for key, grade in relevance.items() if grade > 0}
    hits = len(set(selected) & positive)
    judged = all(key in relevance for key in selected)
    ideal = sorted(relevance.values(), reverse=True)[:cutoff]
    idcg = sum((2**grade - 1) / math.log2(i + 2) for i, grade in enumerate(ideal))
    dcg = (
        sum((2 ** relevance[key] - 1) / math.log2(i + 2) for i, key in enumerate(selected))
        if judged
        else None
    )
    return {
        "retrieved": len(selected),
        "unjudged": sum(key not in relevance for key in selected),
        "precision": Decimal(hits) / len(selected) if selected and judged else None,
        "recall": Decimal(hits) / len(positive) if positive else None,
        "ndcg": Decimal(str(dcg / idcg)) if dcg is not None and idcg else None,
        "scope": "declared_relevance_references_not_universal_corpus_recall",
    }


def assess_episode(
    scenario: EpisodeScenario, observation: EpisodeObservation, *, cutoff=10, user_simulation=None
):
    runner = FixtureRunner(scenario)
    calls, completed, retrievals = {}, [], []
    texts: dict[str, set[str]] = {}
    fixture_errors = False
    duplicates = 0
    unapproved = 0
    for event in observation.events:
        if event.kind == "assistant":
            calls.update({call.call_id: call for call in event.tool_calls})
        elif event.kind == "tool":
            expected = runner.execute(calls[event.call_id])
            if not same_json(expected.event.model_dump(mode="json"), event.model_dump(mode="json")):
                return {
                    "status": "error",
                    "code": "EPISODE_FIXTURE_TRACE_INCONSISTENT",
                    "score": None,
                    "task_success": "not_verified",
                }
            fixture_errors |= event.status in {"fixture_error", "uncertain"}
            duplicates += int(event.duplicate_effect_attempt)
            unapproved += int(
                isinstance(event.result, dict)
                and event.result.get("code") == "EPISODE_TOOL_NOT_APPROVED"
            )
            if event.status == "returned":
                completed.append(calls[event.call_id])
                fixture_tool = runner.tools[event.tool]
                if fixture_tool.kind == "retrieval":
                    content = RetrievalContent.model_validate(event.result)
                    retrievals.append(
                        retrieval_scores(
                            [d.document_id for d in content.documents], scenario.relevance, cutoff
                        )
                    )
                    for document in content.documents:
                        texts.setdefault(document.document_id, set()).add(document.text)
        elif event.kind == "retrieval":
            projected_tool = runner.tools.get(calls[event.call_id].tool)
            if not projected_tool or projected_tool.kind != "retrieval":
                return {
                    "status": "error",
                    "code": "EPISODE_RETRIEVAL_PROJECTION_INCONSISTENT",
                    "score": None,
                    "task_success": "not_verified",
                }
            result = next(
                e for e in observation.events if e.kind == "tool" and e.call_id == event.call_id
            )
            content = RetrievalContent.model_validate(result.result)
            expected = [(d.document_id, d.backend_score) for d in content.documents]
            if [(d.document_id, d.backend_score) for d in event.documents] != expected:
                return {
                    "status": "error",
                    "code": "EPISODE_RETRIEVAL_PROJECTION_INCONSISTENT",
                    "score": None,
                    "task_success": "not_verified",
                }
    if not same_json(observation.final_state, runner.state):
        return {
            "status": "error",
            "code": "EPISODE_REPORTED_FINAL_STATE_INCONSISTENT",
            "score": None,
            "task_success": "not_verified",
        }
    return assess_constraints(
        scenario,
        observation,
        state=runner.state,
        calls=calls,
        completed=completed,
        retrievals=retrievals,
        texts=texts,
        fixture_errors=fixture_errors,
        duplicates=duplicates,
        unapproved=unapproved,
        user_simulation=user_simulation,
    )


def assess_constraints(
    scenario,
    observation,
    *,
    state,
    calls,
    completed,
    retrievals,
    texts,
    fixture_errors,
    duplicates,
    unapproved,
    user_simulation=None,
):
    """Shared declared assertions after the caller verifies its execution boundary.

    This function does not authenticate a transcript or establish an environment.
    Fixture replay and isolated execution must verify their distinct evidence first.
    """
    checks = []

    def check(key, result):
        checks.append(
            {
                "key": key,
                "status": "unknown" if result is None else "passed" if result else "failed",
            }
        )

    unavailable = observation.truncated or observation.outcome in {
        "outage",
        "timeout",
        "cancelled",
        "content_limit",
        "error",
        "policy_denied",
        "budget_limit",
        "unknown_usage",
    }
    check(
        "execution_outcome",
        None if unavailable else observation.outcome in scenario.assertions.accepted_outcomes,
    )

    def present(value):
        return True if value else None if unavailable else False

    def absent(violation):
        return False if violation else None if unavailable else True

    check("approved_tool_boundary", absent(unapproved))
    users = [e.text for e in observation.events if e.kind == "user"]
    declared = len(scenario.user_followups)
    check(
        "declared_user_turn_order",
        None
        if unavailable
        else (
            users[1 : 1 + declared] == scenario.user_followups
            if user_simulation
            else users[1:] == scenario.user_followups
        ),
    )
    if user_simulation:
        receipt = observation.user_simulation
        check(
            "simulated_user_turn_bound",
            None
            if unavailable
            else bool(
                receipt
                and 0
                <= len(users) - 1 - declared
                == receipt.generated_turns
                <= user_simulation.max_turns
                and receipt.status in {"ended", "turn_cap", "target_stopped"}
            ),
        )
    answers_by_turn = {}
    turn = 0
    for event in observation.events:
        if event.kind == "user":
            turn += 1
        elif event.kind == "assistant" and event.finish in {"completed", "refused"}:
            answers_by_turn[turn] = event.text
    for index, constraint in enumerate(scenario.assertions.conversation):
        correct = answers_by_turn.get(constraint.user_turn) in constraint.accepted_answers
        check(
            "conversation_turn_" + str(index + 1), None if unavailable and not correct else correct
        )
    if scenario.assertions.forbid_duplicate_effects:
        check("no_duplicate_effect_attempt", absent(duplicates))
    successful = [call.tool for call in completed]
    attempted = [call.tool for call in calls.values()]
    for i, tool in enumerate(scenario.assertions.required_tools):
        check("required_tool_" + str(i + 1), present(tool in successful))
    for i, tool in enumerate(scenario.assertions.forbidden_tools):
        check("forbidden_tool_" + str(i + 1), absent(tool in attempted))
    if scenario.assertions.allowed_paths:
        check(
            "allowed_tool_path",
            None if unavailable else successful in scenario.assertions.allowed_paths,
        )
    for i, rule in enumerate(scenario.assertions.arguments):
        matches = sum(
            call.tool == rule.tool and same_json(call.arguments, rule.arguments)
            for call in completed
        )
        check("tool_arguments_" + str(i + 1), present(matches >= rule.minimum_occurrences))
    for i, (pointer, value) in enumerate(scenario.assertions.final_state.items()):
        try:
            correct = same_json(state_value(state, pointer), value)
        except (KeyError, IndexError):
            correct = False
        check("final_state_" + str(i + 1), None if unavailable else correct)
    if scenario.assertions.accepted_answers:
        check(
            "declared_answer_reference",
            None
            if unavailable
            else observation.final_answer in scenario.assertions.accepted_answers,
        )

    def average(key):
        values = [row[key] for row in retrievals]
        return (
            sum(values, Decimal(0)) / len(values)
            if values and all(v is not None for v in values)
            else None
        )

    retrieval = {key: average(key) for key in ("precision", "recall", "ndcg")}
    for name, minimum in (
        ("precision", scenario.assertions.min_retrieval_precision),
        ("recall", scenario.assertions.min_retrieval_recall),
        ("ndcg", scenario.assertions.min_ndcg),
    ):
        if minimum is not None:
            check(
                "retrieval_" + name, None if retrieval[name] is None else retrieval[name] >= minimum
            )
    citations = {(c.document_id, c.quote) for c in observation.citations}
    supported = sum(
        any(quote in text for text in texts.get(document, set())) for document, quote in citations
    )
    citation_support = Decimal(supported) / len(citations) if citations else None
    if scenario.assertions.min_citation_support is not None:
        check(
            "literal_citation_support",
            None
            if citation_support is None
            else citation_support >= scenario.assertions.min_citation_support,
        )
    if fixture_errors:
        status = "error"
    elif any(c["status"] == "failed" for c in checks):
        status = "failed"
    elif any(c["status"] == "unknown" for c in checks):
        status = "unknown"
    else:
        status = "passed"
    return {
        "status": status,
        "score": 1 if status == "passed" else 0 if status == "failed" else None,
        "checks": checks,
        "retrieval": {k: str(v) if v is not None else None for k, v in retrieval.items()},
        "retrieval_calls": len(retrievals),
        "unjudged_retrieval_documents": sum(r["unjudged"] for r in retrievals),
        "literal_citation_support": str(citation_support) if citation_support is not None else None,
        "citation_semantics": "not_verified",
        "task_success": "declared_synthetic_contract_only",
        "evidence": "synthetic_fixture_replay_not_production_state",
        "population_coverage": "unknown",
    }
