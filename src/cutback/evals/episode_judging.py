"""Evidence-constrained semantic proxy inputs and responses, never customer verification."""

import json

from cutback.core.episode_judging import (
    EPISODE_JUDGE_INSTRUCTION,
    EPISODE_JUDGE_SCHEMA,
    EpisodeJudgeResponse,
)
from cutback.core.episodes import RetrievalContent, bounded_episode_value
from cutback.core.request_profiles import RequestContract
from cutback.evals.agent_executor import strict_json
from cutback.evals.episode_metrics import assess_episode


def episode_judge_model(model):
    """Freeze one owned nonstreaming text request; callers still need native qualification."""
    from cutback.evals.experiments import ModelSpec

    if model.provider != "openai" or model.agent_turn or model.routes or model.prompt_cache:
        raise ValueError("EPISODE_JUDGE_REQUIRES_QUALIFIED_OPENAI_TEXT_MODEL")
    value = model.model_dump(mode="json")
    value["instruction"] = EPISODE_JUDGE_INSTRUCTION
    value["request_contract"] = RequestContract(
        sdk_surface="responses.create",
        parameters=["input"],
        payload={"op": "parameter", "name": "input"},
        options={
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "cutback_episode_judgments",
                    "strict": True,
                    "schema": EPISODE_JUDGE_SCHEMA,
                }
            }
        },
    ).model_dump(mode="json")
    return ModelSpec.model_validate(value)


def judging_document(specification, scenario, observation, case):
    """Only returned retrieval data, observed conversation and requested references.

    Unreturned tool fixtures, hidden state assertions and unrequested references
    are deliberately excluded. The deterministic runner's trace must be valid.
    """
    if (
        observation.outcome != "completed"
        or observation.truncated
        or observation.final_answer is None
    ):
        raise ValueError("EPISODE_SEMANTIC_COMPLETE_ANSWER_REQUIRED")
    if assess_episode(scenario, observation).get("status") == "error":
        raise ValueError("EPISODE_SEMANTIC_OBSERVATION_INTEGRITY_REQUIRED")
    kinds = {c.kind for c in specification.criteria}
    sources = {}
    if "conversation_constraints" in kinds:
        texts = [
            scenario.system,
            *[e.text for e in observation.events if e.kind in {"user", "assistant"}],
        ]
        for index, text in enumerate(texts):
            if text:
                sources[f"conversation:{index}"] = text
    if "answer_correctness" in kinds:
        reference = case.get("gold") if case.get("gold") is not None else case.get("reference")
        if reference is None and scenario.assertions.accepted_answers:
            reference = scenario.assertions.accepted_answers
        if reference is None:
            raise ValueError("EPISODE_SEMANTIC_CORRECTNESS_REFERENCE_REQUIRED")
        sources["reference:0"] = (
            reference
            if isinstance(reference, str)
            else json.dumps(reference, ensure_ascii=True, sort_keys=True, allow_nan=False)
        )
    if "retrieval_groundedness" in kinds:
        retrieval_calls = {e.call_id for e in observation.events if e.kind == "retrieval"}
        index = 0
        for event in observation.events:
            if (
                event.kind == "tool"
                and event.call_id in retrieval_calls
                and event.status == "returned"
            ):
                for document in RetrievalContent.model_validate(event.result).documents:
                    if index >= 1000:
                        raise ValueError("EPISODE_SEMANTIC_SOURCE_BOUND")
                    sources[f"retrieved:{index}"] = document.text
                    index += 1
        if not index:
            raise ValueError("EPISODE_SEMANTIC_RETURNED_RETRIEVAL_REQUIRED")
    allowed = {
        c.key: [
            source
            for source in sources
            if source.startswith(
                {
                    "answer_correctness": "reference:",
                    "retrieval_groundedness": "retrieved:",
                    "conversation_constraints": "conversation:",
                }[c.kind]
            )
        ]
        for c in specification.criteria
    }
    value = {
        "version": specification.version,
        "task_inputs": [e.text for e in observation.events if e.kind == "user"],
        "final_answer": observation.final_answer,
        "criteria": [
            {
                "key": c.key,
                "kind": c.kind,
                "rubric": c.judge_rubric,
                "permitted_source_ids": allowed[c.key],
            }
            for c in specification.criteria
        ],
        "sources": [{"source_id": key, "text": text} for key, text in sources.items()],
    }
    bounded_episode_value(value, max_bytes=192000)
    return json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False), sources, allowed


def assess_judgment(specification, text, sources, allowed):
    """Return content-free proxy scores; even grounded citations do not prove the score."""
    try:
        response = EpisodeJudgeResponse.model_validate(strict_json(text))
    except ValueError:
        raise ValueError("EPISODE_JUDGE_INVALID_RESPONSE") from None
    expected = {c.key: c for c in specification.criteria}
    if len(response.checks) != len(expected) or {c.key for c in response.checks} != set(expected):
        raise ValueError("EPISODE_JUDGE_EXACT_CRITERIA_REQUIRED")
    results = []
    for vote in response.checks:
        if len({(c.source_id, c.quote) for c in vote.citations}) != len(vote.citations) or any(
            c.source_id not in allowed[vote.key]
            or not c.quote.strip()
            or c.quote not in sources[c.source_id]
            for c in vote.citations
        ):
            raise ValueError("EPISODE_JUDGE_CITATION_NOT_IN_PERMITTED_OBSERVED_SOURCE")
        results.append(
            {
                "key": vote.key,
                "kind": expected[vote.key].kind,
                "status": "passed" if vote.score >= expected[vote.key].minimum else "failed",
                "score": vote.score,
                "minimum": expected[vote.key].minimum,
                "verified_quote_count": len(vote.citations),
                "evidence": (
                    "model_assessment_with_exact_source_quotes_not_independent_verification"
                ),
            }
        )
    return {
        "version": specification.version,
        "checks": results,
        "evidence_tier": 3,
        "semantic_quality": "NOT VERIFIED",
        "raw_content_in_metadata": False,
    }


def perform_episode_judging(plan, scenario, observation, case, ledger, executor, check_authority):
    """One separately reserved judge request. Failures remain unknown and never retry."""
    specification = plan.episode_execution.semantic_judging
    raw = None
    try:
        text, sources, allowed = judging_document(specification, scenario, observation, case)
        if check_authority:
            check_authority()
        if hasattr(executor, "prepare"):
            executor.prepare(plan.judge.provider)
        result = ledger.call(executor, plan.judge, text, traffic_kind="judge")
        raw = result.output
        if check_authority:
            check_authority()
        if not result.complete:
            raise ValueError("EPISODE_JUDGE_INCOMPLETE_RESPONSE")
        return assess_judgment(specification, result.output, sources, allowed), raw
    except Exception:
        if ledger.receipt_uncertain:
            raise ValueError("CALL_RECEIPT_WRITE_UNCERTAIN_NO_REQUEST_RETRY") from None
        return {
            "version": specification.version,
            "checks": [
                {
                    "key": c.key,
                    "kind": c.kind,
                    "status": "unknown",
                    "score": None,
                    "minimum": c.minimum,
                    "verified_quote_count": 0,
                    "evidence": "unavailable_or_invalid_judge_proxy",
                }
                for c in specification.criteria
            ],
            "evidence_tier": 3,
            "semantic_quality": "NOT VERIFIED",
            "raw_content_in_metadata": False,
            "stop_execution": not ledger.billing_complete,
        }, raw


def combine_assessment(assessment, judged):
    """A model score cannot repair a deterministic violation or missing evidence."""
    checks = [
        *assessment.get("checks", []),
        *[{"key": "semantic_" + c["key"], "status": c["status"]} for c in judged["checks"]],
    ]
    state = (
        "error"
        if assessment["status"] == "error"
        else "failed"
        if any(c["status"] == "failed" for c in checks)
        else "unknown"
        if assessment["status"] == "unknown" or any(c["status"] == "unknown" for c in checks)
        else "passed"
    )
    return {
        **assessment,
        "status": state,
        "score": 1 if state == "passed" else 0 if state == "failed" else None,
        "checks": checks,
        "semantic_judging": judged,
        "task_success": "declared_fixture_and_model_proxy_only",
    }
