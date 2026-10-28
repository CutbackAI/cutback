"""Owned semantic judge plans and strict synthetic responses."""

import json
from uuid import uuid4

from cutback.evals.episode_judging import episode_judge_model
from cutback.evals.experiments import ExperimentPlan, ModelSpec


def with_semantic_judge(plan, **changes):
    value = plan.model_dump(mode="json")
    value.update(
        evaluator_version="deterministic-v5",
        content_nonce=str(uuid4()),
        judge=episode_judge_model(
            ModelSpec(
                key="semantic-judge",
                provider="openai",
                model="gpt-4.1-mini",
                max_input_tokens=64000,
                max_output_tokens=1024,
            )
        ).model_dump(mode="json"),
        approved_providers=sorted(set(plan.approved_providers) | {"openai"}),
        **changes,
    )
    value["episode_execution"].update(
        version="native-fixture-episode-v2",
        semantic_judging={
            "criteria": [
                {
                    "key": "answer-correctness",
                    "kind": "answer_correctness",
                    "judge_rubric": (
                        "PRIVATE_RUBRIC_CANARY: assess answer agreement with the reference."
                    ),
                    "minimum": 1.0,
                }
            ],
        },
    )
    return ExperimentPlan.model_validate(value)


def semantic_response(body, mode="pass"):
    body["output"][0]["content"][0]["text"] = json.dumps(
        {
            "checks": [
                {
                    "key": "answer-correctness",
                    "score": 0 if mode == "fail" else 1,
                    "citations": [
                        {
                            "source_id": "reference:0",
                            "quote": "invented" if mode == "invalid" else "Urgent",
                        }
                    ],
                }
            ]
        }
    )
    return body
