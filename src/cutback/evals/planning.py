"""Automatic constrained candidate planning from metadata. Never executes inference."""

from decimal import Decimal
from uuid import uuid4

from cutback.core.episode_judging import EpisodeJudgingSpec
from cutback.core.isolated_episodes import (
    GuardedRecordExecution,
    IsolatedRecordExecution,
    IsolatedSecurityExecution,
)
from cutback.core.security_suites import SecurityEvaluationSpec
from cutback.evals.experiments import (
    Budget,
    EpisodeExecutionSpec,
    ExperimentPlan,
    ModelSpec,
    provider_models,
)
from cutback.evals.metrics import MetricSpec
from cutback.pricing import Catalog

VALIDATED_MODELS = {
    "openai": ("gpt-4.1", "gpt-4.1-mini"),
    "anthropic": ("claude-sonnet-4-6", "claude-haiku-4-5"),
}


def draft_plan(
    dataset,
    baseline: ModelSpec,
    *,
    candidates=None,
    minimum=None,
    metric_kind=None,
    max_usd="0",
    max_requests=100,
    repeats=1,
    max_wall_seconds=300,
    pilot_samples=0,
    pilot_minimum=None,
    criteria=None,
    retain_case_outputs=False,
    episode_judging: EpisodeJudgingSpec | None = None,
    episode_judge: ModelSpec | None = None,
    episode_max_turns=32,
    isolated_tools: IsolatedRecordExecution
    | IsolatedSecurityExecution
    | GuardedRecordExecution
    | None = None,
    security_suite: SecurityEvaluationSpec | None = None,
):
    provenance = dataset["provenance"]
    if (provenance.get("security_suite") is not None) != (security_suite is not None) or (
        security_suite is not None
        and (
            isolated_tools is None
            or provenance["security_suite"] != security_suite.suite.model_dump(mode="json")
        )
    ):
        raise ValueError("SECURITY_SUITE_REQUIRES_EXPLICIT_MATCHING_SCOPED_EVALUATION")
    catalog = Catalog()
    if candidates is None:
        baseline_rate = catalog.rate_info(baseline.provider, baseline.model)["rate"]
        if not baseline_rate:
            raise ValueError("BASELINE_PRICE_UNKNOWN_EXPLICIT_CANDIDATE_REQUIRED")
        candidates = []
        for name in VALIDATED_MODELS.get(baseline.provider, ()):
            rate = catalog.rate_info(baseline.provider, name)["rate"]
            if (
                rate
                and Decimal(rate["input"]) < Decimal(baseline_rate["input"])
                and Decimal(rate["output"]) <= Decimal(baseline_rate["output"])
            ):
                candidates.append(
                    baseline.model_copy(
                        update={"key": "candidate-" + str(len(candidates) + 1), "model": name}
                    )
                )
    if not candidates:
        raise ValueError("NO_COMPATIBLE_CHEAPER_MODEL_SUBMIT_REVIEWED_PROMPT_VARIANT")
    inferred = provenance.get("task_inference", {}).get("suggested_metric", "nonempty")
    kind = metric_kind or inferred
    if (
        isolated_tools is not None
        and kind != "episode_contract"
        or (
            provenance.get("task_inference", {}).get("kind") == "isolated_record_episode"
            and isolated_tools is None
        )
    ):
        raise ValueError("ISOLATED_RECORD_EPISODE_REQUIRES_EXPLICIT_BROKER_APPROVAL_CONTRACT")
    if episode_judging is not None or episode_judge is not None:
        if kind != "episode_contract" or episode_judging is None or episode_judge is None:
            raise ValueError("EPISODE_JUDGING_REQUIRES_TYPED_EPISODES_CRITERIA_AND_JUDGE")
        from cutback.evals.episode_judging import episode_judge_model

        episode_judge = episode_judge_model(episode_judge)
        assert episode_judge.provider == "openai"
    independent = provenance["independent_labels"] and minimum is not None
    semantic = kind in {
        "exact_label",
        "field_f1",
        "retrieval_recall",
        "ndcg",
        "rank_agreement",
        "numeric_match",
    }
    if semantic and not independent:
        kind = "nonempty"
    if minimum is None and kind != "episode_contract":
        kind = "nonempty"
    maximum_bytes = provenance.get("max_input_bytes", 0)
    for model in [baseline, *candidates]:
        if (
            maximum_bytes
            and maximum_bytes + len(model.instruction.encode()) + 4096 > model.max_input_tokens
        ):
            raise ValueError("DATASET_EXCEEDS_MODEL_INPUT_BOUND")
    return ExperimentPlan(
        workflow=dataset["workflow"],
        dataset_hash=dataset["hash"],
        baseline=baseline,
        candidates=candidates,
        metric=MetricSpec(kind=kind, minimum=minimum if minimum is not None else 1),
        budget=Budget(
            **{
                "max_usd": max_usd,
                "max_requests": max_requests,
                "max_wall_seconds": max_wall_seconds,
            }
        ),
        approved_providers=sorted(
            {
                target.provider
                for model in [baseline, *candidates]
                for target in provider_models(model)
                if target.provider != "router"
            }
            | ({"openai"} if episode_judge else set())
        ),
        repeats=repeats,
        pilot_samples=pilot_samples,
        pilot_minimum=pilot_minimum,
        criteria=criteria or [],
        retain_case_outputs=retain_case_outputs,
        evaluator_version="deterministic-v11"
        if isinstance(isolated_tools, GuardedRecordExecution) and security_suite is not None
        else "deterministic-v10"
        if isinstance(isolated_tools, GuardedRecordExecution)
        else "deterministic-v9"
        if security_suite
        else "deterministic-v8"
        if isolated_tools
        else "deterministic-v5"
        if episode_judging
        else "deterministic-v4"
        if kind == "episode_contract"
        else "deterministic-v3",
        content_nonce=uuid4() if episode_judging or isolated_tools else None,
        judge=episode_judge,
        episode_execution=EpisodeExecutionSpec(
            version="native-isolated-record-guarded-security-v1"
            if isinstance(isolated_tools, GuardedRecordExecution) and security_suite is not None
            else "native-isolated-record-guarded-v1"
            if isinstance(isolated_tools, GuardedRecordExecution)
            else "native-isolated-record-security-v1"
            if security_suite
            else "native-isolated-record-episode-v1"
            if isolated_tools
            else "native-fixture-episode-v2"
            if episode_judging
            else "native-fixture-episode-v1",
            max_turns=episode_max_turns,
            max_tool_calls=100,
            semantic_judging=episode_judging,
            isolated_tools=isolated_tools,
            security_suite=security_suite,
        )
        if kind == "episode_contract"
        else None,
    )
