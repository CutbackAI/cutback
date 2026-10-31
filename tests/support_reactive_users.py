"""Synthetic reactive fixtures importable independently of pytest collection order."""

from uuid import uuid4

import httpx
from support_evaluation import plan_for
from support_native_agent import response_body

from cutback.evals.experiments import ExperimentPlan, ModelSpec
from cutback.evals.user_simulation import user_simulation_model


def reactive_plan(**changes):
    raw = plan_for(
        {"immutable_hash": "a" * 64},
        baseline={"key": "baseline", "provider": "openai", "model": "gpt-4.1-mini"},
        candidates=[{"key": "candidate", "provider": "openai", "model": "gpt-4.1-mini"}],
        approved_providers=["openai"],
        metric={"kind": "episode_contract", "minimum": 1},
        evaluator_version="deterministic-v4",
        episode_execution={"max_turns": 3},
    ).model_dump(mode="json")
    raw.update(
        evaluator_version="deterministic-v17",
        content_nonce=str(uuid4()),
        episode_execution={
            "version": "native-fixture-reactive-user-v1",
            "max_turns": 3,
            "user_simulation": {
                "model": user_simulation_model(
                    ModelSpec(
                        key="simulated_user",
                        provider="openai",
                        model="gpt-4.1-mini",
                        max_input_tokens=32768,
                    )
                ).model_dump(mode="json"),
                "persona": "skeptical",
                "max_turns": 1,
            },
        },
    )
    raw.update(changes)
    return ExperimentPlan.model_validate(raw)


def native_text(text, *, usage=True, complete=True):
    value = response_body("responses.create")
    value["output"][0]["content"][0]["text"] = text
    if not usage:
        value.pop("usage", None)
    if not complete:
        value["status"] = "incomplete"
    return httpx.Response(200, json=value)
