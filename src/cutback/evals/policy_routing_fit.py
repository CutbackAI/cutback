"""Train-only fitting against the actual current router and its preserved tail."""

import time
from collections import defaultdict
from decimal import Context, Decimal, localcontext

from cutback.evals.experiments import ModelSpec, RouteSpec
from cutback.evals.learned_routing import RoutingFitOptions, SourcePolicyRoutingTrainingEvidence


def fit_current_routes(
    evidence: SourcePolicyRoutingTrainingEvidence,
    options: RoutingFitOptions,
    *,
    clock=time.monotonic,
):
    with localcontext(Context(prec=80)):
        return _fit_current_routes(evidence, options, clock=clock)


def _fit_current_routes(evidence, options, *, clock):
    """One native leaf per request; no replay, inference or held-out selection.

    Current routed observations are the comparator, never invented observations
    of a historical native anchor. Only actually paired plain controls can form
    the fitted prefix. Unobserved larger inputs keep the current source router.
    """
    if type(evidence) is not SourcePolicyRoutingTrainingEvidence:
        raise ValueError("POLICY_ROUTING_CURRENT_REFERENCE_EVIDENCE_REQUIRED")
    deadline = clock() + options.max_fit_seconds
    models = {model.key: model for model in evidence.models}
    reference = models[evidence.baseline_key]
    keys = sorted(set(models) - {evidence.baseline_key})
    rows: dict[str, dict] = defaultdict(dict)
    groups = set()
    for row in evidence.observations:
        rows[row.case_sha256][row.model_key] = row
        groups.add(row.group_sha256)
    if len(groups) < options.min_training_groups:
        raise ValueError("ROUTING_TRAINING_GROUPS_INSUFFICIENT")
    baseline = [case[evidence.baseline_key] for case in rows.values()]
    if any(row.known_cost_usd is None for row in baseline):
        raise ValueError("ROUTING_TRAINING_BASELINE_COST_UNKNOWN")
    maximum = reference.routes[-1].max_input_bytes
    if any(row.input_bytes > maximum for row in baseline):
        raise ValueError("POLICY_ROUTING_TRAINING_OUTSIDE_CURRENT_DOMAIN")
    buckets = defaultdict(list)
    for case in rows.values():
        buckets[case[evidence.baseline_key].input_bytes].append(case)
    bounds = sorted(buckets)
    # No gap is filled using the historical anchor. This tail preserves the
    # current route's exact native target, including instructions/output limits.
    tail = [route for route in reference.routes if route.max_input_bytes > bounds[-1]]
    counts = [0]
    costs = {key: [Decimal(0)] for key in keys}
    invalid = {key: [0] for key in keys}
    for bound in bounds:
        cases = buckets[bound]
        counts.append(counts[-1] + len(cases))
        for key in keys:
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
    # Retain the final target in the DP state so a tail merge cannot cause the
    # cheapest otherwise feasible alternative to disappear during pruning.
    states: dict[tuple[int, int, str], tuple[Decimal, tuple[tuple[int, str], ...]]] = {
        (0, 0, ""): (Decimal(0), ())
    }
    size = len(bounds)
    for leaves in range(1, options.max_routes + 1):
        for end in range(1, size + 1):
            if clock() >= deadline:
                raise ValueError("ROUTING_FIT_COMPUTE_BOUND")
            for key in keys:
                best = None
                for start in range(end):
                    if (
                        counts[end] - counts[start] < options.min_leaf_cases
                        or invalid[key][end] != invalid[key][start]
                    ):
                        continue
                    for last in ["", *keys]:
                        previous = states.get((leaves - 1, start, last))
                        if previous is None or last == key:
                            continue
                        trial = (
                            previous[0] + costs[key][end] - costs[key][start],
                            (*previous[1], (end, key)),
                        )
                        if best is None or trial < best:
                            best = trial
                if best is not None:
                    states[leaves, end, key] = best
    choices = []
    for (leaves, end, _), (cost, path) in states.items():
        if end != size or not leaves:
            continue
        routes = [
            RouteSpec(max_input_bytes=bounds[index - 1], target=models[key]) for index, key in path
        ]
        for route in tail:
            # Native aliases do not change behavior and must not prevent a merge.
            if routes[-1].target.model_dump(exclude={"key"}) == route.target.model_dump(
                exclude={"key"}
            ):
                routes[-1] = RouteSpec(
                    max_input_bytes=route.max_input_bytes, target=routes[-1].target
                )
            else:
                routes.append(route)
        if len(routes) <= options.max_routes:
            choices.append((cost, len(routes), path, routes))
    if not choices:
        raise ValueError("ROUTING_TRAINING_NO_FEASIBLE_PARTITION")
    cost, _, _, routes = min(choices, key=lambda value: value[:3])
    baseline_cost = sum((row.known_cost_usd or Decimal(0) for row in baseline), Decimal(0))
    if cost >= baseline_cost:
        raise ValueError("ROUTING_TRAINING_NO_LOWER_COST_FEASIBLE_POLICY")
    candidate = ModelSpec(
        key="learned-current-size-route",
        provider="router",
        model="train-fitted-current-input-size-v1",
        routes=routes,
    )
    from cutback.evals.datasets import digest

    return {
        "version": "cutback.source-policy-current-router-fit.v1",
        "training_evidence_sha256": digest(evidence.model_dump(mode="json")),
        "options": options.model_dump(mode="json"),
        "candidate": candidate.model_dump(mode="json"),
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
        "unobserved_input_tail_preserves_current_router": True,
    }
