"""Fit bounded input-size routes from complete, train-only paired observations.

This module performs no inference. Its training optimum is not held-out quality
evidence. The server must authenticate the source observations before calling it.
"""

import time
from collections import defaultdict
from collections.abc import Callable
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, model_validator

from cutback.core.models import Identifier, StrictModel
from cutback.evals.datasets import digest
from cutback.evals.experiments import ModelSpec, RouteSpec, provider_models, qualified_native_model

Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class RoutingFitOptions(StrictModel):
    version: Literal["cutback.input-size-routing-fit.v1"] = "cutback.input-size-routing-fit.v1"
    max_routes: Annotated[StrictInt, Field(ge=2, le=8)] = 4
    min_leaf_cases: Annotated[StrictInt, Field(ge=1, le=256)] = 5
    min_training_groups: Annotated[StrictInt, Field(ge=2, le=256)] = 3
    max_fit_seconds: Annotated[StrictInt, Field(ge=1, le=30)] = 10


class RoutingObservation(StrictModel):
    case_sha256: Sha256
    group_sha256: Sha256
    model_key: Identifier
    input_bytes: Annotated[StrictInt, Field(ge=1, le=95904)]
    acceptable: StrictBool
    known_cost_usd: Decimal | None = Field(default=None, ge=0, max_digits=24, decimal_places=12)


class RoutingTrainingEvidence(StrictModel):
    split: Literal["train"]
    dataset_sha256: Sha256
    source_plan_sha256: Sha256
    source_run_id: UUID
    baseline_key: Identifier
    models: list[ModelSpec] = Field(min_length=2, max_length=5)
    observations: list[RoutingObservation] = Field(min_length=2, max_length=1280)

    @model_validator(mode="after")
    def paired(self):
        keys = [model.key for model in self.models]
        if len(keys) != len(set(keys)) or self.baseline_key not in keys:
            raise ValueError("ROUTING_TRAINING_MODEL_IDENTITIES_INVALID")
        seen = set()
        identities: dict[str, tuple[str, int]] = {}
        if not self.source_run_id.int:
            raise ValueError("ROUTING_TRAINING_SOURCE_RUN_ID_REQUIRED")
        for row in self.observations:
            identity = (row.case_sha256, row.model_key)
            if identity in seen or row.model_key not in keys:
                raise ValueError("ROUTING_TRAINING_DUPLICATE_OR_UNKNOWN_OBSERVATION")
            seen.add(identity)
            shared = (row.group_sha256, row.input_bytes)
            if identities.setdefault(row.case_sha256, shared) != shared:
                raise ValueError("ROUTING_TRAINING_CASE_IDENTITY_CONFLICT")
        if len(identities) > 256 or seen != {(case, key) for case in identities for key in keys}:
            raise ValueError("ROUTING_TRAINING_REQUIRES_BOUNDED_COMPLETE_PAIRING")
        for model in self.models:
            if model.provider not in {"openai", "anthropic"} or model.routes:
                raise ValueError("ROUTING_TRAINING_REQUIRES_QUALIFIED_NATIVE_TARGETS")
            qualified_native_model(model)
        return self


def fit_routes(
    evidence: RoutingTrainingEvidence,
    options: RoutingFitOptions,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    """Find the cheapest feasible contiguous partition with a baseline tail.

    Equal byte lengths share a route. Each leaf must pass every training case's
    owner's required checks. Unknown costs cannot win. Ties prefer fewer leaves
    and then the canonical model order. Every proposal still needs fresh
    validation and a separately approved untouched confirmation.
    """
    deadline = clock() + options.max_fit_seconds
    models = {model.key: model for model in evidence.models}
    baseline = models[evidence.baseline_key]
    groups = {row.group_sha256 for row in evidence.observations}
    if len(groups) < options.min_training_groups:
        raise ValueError("ROUTING_TRAINING_GROUPS_INSUFFICIENT")
    rows: defaultdict[str, dict[str, RoutingObservation]] = defaultdict(dict)
    for row in evidence.observations:
        rows[row.case_sha256][row.model_key] = row
    baseline_rows = [case[evidence.baseline_key] for case in rows.values()]
    if any(row.known_cost_usd is None for row in baseline_rows):
        raise ValueError("ROUTING_TRAINING_BASELINE_COST_UNKNOWN")
    baseline_bound = min(
        95904, baseline.max_input_tokens - len(baseline.instruction.encode()) - 4096
    )
    if baseline_bound < 1 or any(row.input_bytes > baseline_bound for row in baseline_rows):
        raise ValueError("ROUTING_TRAINING_OUTSIDE_BASELINE_INPUT_BOUND")
    buckets = defaultdict(list)
    for case in rows.values():
        buckets[case[evidence.baseline_key].input_bytes].append(case)
    bounds = sorted(buckets)
    ordered_keys = [evidence.baseline_key, *sorted(set(models) - {evidence.baseline_key})]
    # Prefix sums make each of the bounded O(routes * bins² * models)
    # candidate partitions independent of the number of rows inside a bin.
    counts = [0]
    costs = {key: [Decimal(0)] for key in ordered_keys}
    invalid = {key: [0] for key in ordered_keys}
    for bound in bounds:
        cases = buckets[bound]
        counts.append(counts[-1] + len(cases))
        for key in ordered_keys:
            target = models[key]
            fits = bound + len(target.instruction.encode()) + 4096 <= target.max_input_tokens
            observations = [case[key] for case in cases]
            costs[key].append(
                costs[key][-1]
                + sum((row.known_cost_usd or Decimal(0) for row in observations), Decimal(0))
            )
            invalid[key].append(
                invalid[key][-1]
                + sum(
                    not fits or not row.acceptable or row.known_cost_usd is None
                    for row in observations
                )
            )
    # Leave room for an out-of-training-range baseline fallback unless it can
    # merge into the last leaf. It remains an unvalidated fallback.
    states: dict[tuple[int, int], tuple[Decimal, tuple[tuple[int, int], ...]]] = {
        (0, 0): (Decimal(0), ())
    }
    size = len(bounds)
    for leaves in range(1, options.max_routes + 1):
        for end in range(1, size + 1):
            if clock() >= deadline:
                raise ValueError("ROUTING_FIT_COMPUTE_BOUND")
            best = None
            for start in range(end):
                previous = states.get((leaves - 1, start))
                if previous is None or counts[end] - counts[start] < options.min_leaf_cases:
                    continue
                for index, key in enumerate(ordered_keys):
                    if (
                        end == size
                        and leaves == options.max_routes
                        and key != evidence.baseline_key
                        and bounds[-1] < baseline_bound
                    ):
                        continue
                    if invalid[key][end] != invalid[key][start]:
                        continue
                    cost = previous[0] + costs[key][end] - costs[key][start]
                    path = (*previous[1], (end, index))
                    candidate = (cost, path)
                    if best is None or candidate < best:
                        best = candidate
            if best is not None:
                states[leaves, end] = best
    choices = [
        (cost, leaves, path)
        for (leaves, end), (cost, path) in states.items()
        if end == size and leaves > 0
    ]
    if not choices:
        raise ValueError("ROUTING_TRAINING_NO_FEASIBLE_PARTITION")
    cost, _, path = min(choices)
    routes: list[RouteSpec] = []
    for end, index in path:
        key = ordered_keys[index]
        bound = bounds[end - 1]
        if routes and routes[-1].target.key == key:
            routes[-1] = RouteSpec(max_input_bytes=bound, target=models[key])
        else:
            routes.append(RouteSpec(max_input_bytes=bound, target=models[key]))
    if routes[-1].target.key == evidence.baseline_key:
        routes[-1] = RouteSpec(max_input_bytes=baseline_bound, target=baseline)
    elif bounds[-1] < baseline_bound:
        routes.append(RouteSpec(max_input_bytes=baseline_bound, target=baseline))
    model = ModelSpec(
        key="learned-size-route",
        provider="router",
        model="train-fitted-input-size-v1",
        routes=routes,
    )
    provider_models(model)
    baseline_cost = sum((row.known_cost_usd or Decimal(0) for row in baseline_rows), Decimal(0))
    if cost >= baseline_cost:
        raise ValueError("ROUTING_TRAINING_NO_LOWER_COST_FEASIBLE_POLICY")
    return {
        "version": options.version,
        "training_evidence_sha256": digest(evidence.model_dump(mode="json")),
        "options": options.model_dump(mode="json"),
        "candidate": model.model_dump(mode="json"),
        "training_cases": len(rows),
        "training_groups": len(groups),
        "fitted_training_native_cost_usd": str(cost),
        "baseline_training_native_cost_usd": str(baseline_cost),
        "fitted_training_native_cost_difference_usd": str(baseline_cost - cost),
        "training_fit_replays_paired_observations": True,
        "routing_extra_provider_requests": 0,
        "routing_hosting_cost_usd": None,
        "heldout_quality_claim": False,
        "requires_validation_and_untouched_confirmation": True,
        "source_patch_eligible": False,
    }
