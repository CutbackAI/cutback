"""Independent ticket application for the common source-policy workflow."""

import json
from pathlib import Path


def fixture(*, async_consumer=False, cached_current=False):
    from cutback.core.exact_cache import ExactCacheExecution
    from cutback.core.improvement_policy import SourceImprovementPolicy
    from cutback.core.policy_producer import ASYNC_PROGRAM, PROGRAM
    from cutback.demo_routing import sample_cases
    from cutback.evals.experiments import ModelSpec

    if cached_current and async_consumer:
        raise ValueError("POLICY_SOURCE_ASYNC_CACHE_RUNTIME_UNQUALIFIED")

    baseline = ModelSpec(
        key="application-baseline",
        provider="openai",
        model="gpt-4.1",
        instruction="Classify the declared ticket priority. Return only Urgent or Normal.",
        max_output_tokens=64,
    )
    policy = SourceImprovementPolicy(
        workflow="synthetic-source-policy",
        max_input_bytes=4000,
        baseline=baseline,
        strategy=baseline,
        cache_execution=ExactCacheExecution.model_validate(
            {
                "freshness_ref": "owned-immutable-synthetic-tickets",
                "ttl_seconds": 60,
                "content_retention_authorized": True,
                "immutable_or_safely_invalidated": True,
                "operation_cost_upper_usd": "0.000000001",
                "operation_cost_basis_ref": "owned-synthetic-declared-upper",
            }
        )
        if cached_current
        else None,
    )
    cases = sample_cases()
    if cached_current:
        cases = [
            {**case, "id": case["id"] + "-repeat-" + str(index)}
            for case in cases
            for index in range(2)
        ]
    return {
        "app.py": ASYNC_PROGRAM if async_consumer else PROGRAM,
        "policy.json": policy.configuration_bytes().decode(),
        "tickets.jsonl": "".join(json.dumps(case) + "\n" for case in cases),
        "check_application.py": Path(__file__)
        .with_name("policy_async_check.py" if async_consumer else "policy_check.py")
        .read_text("utf-8"),
        "observe_application.py": Path(__file__).with_name("policy_observe.py").read_text("utf-8"),
        ".gitignore": "__pycache__/\n.cutback/\n.venv/\n",
    }
