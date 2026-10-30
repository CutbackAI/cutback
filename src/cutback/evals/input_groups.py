"""Bounded, in-memory native-input grouping; never persist rendered inputs or hashes."""

import hashlib
import json
import time

from cutback.evals.datasets import normalized_input

MAX_PROJECTED_BYTES = 32 * 1048576


class InputGroupingViolation(ValueError):
    """Fixed diagnostics safe for metadata-only execution failures."""


def requires_projection(plan):
    from cutback.evals.experiments import provider_models

    return (
        bool(plan.compression_execution)
        or not (plan.episode_execution or plan.retrieval_execution)
        and any(
            target.request_contract
            for model in [plan.baseline, *plan.candidates]
            for target in provider_models(model)
        )
    )


def _normalized(value):
    if isinstance(value, str):
        return normalized_input(value)
    if isinstance(value, list):
        return [_normalized(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalized(item) for key, item in value.items()}
    return value


def effective_input_groups(plan, dataset, *, check=None, deadline=None, prepare_input=None):
    """Merge existing families when any frozen model sends equal normalized inputs.

    This checks the whole frozen corpus, before sampling or billing. It does not
    establish historical independence across other datasets or request templates.
    Existing raw/customer family identities remain authoritative and never split.
    """
    from cutback.evals.experiments import route_target

    models = [plan.baseline, *plan.candidates]
    if not requires_projection(plan):
        return dataset, None
    if plan.compression_execution and prepare_input is None:
        from cutback.evals.compression import CompressionProcessor

        prepare_input = CompressionProcessor(
            plan.compression_execution, check=check, deadline=deadline
        ).prepare
    items = dataset["items"]
    parents = {item["group_hash"]: item["group_hash"] for item in items}

    def root(group):
        while group != parents[group]:
            parents[group] = parents[parents[group]]
            group = parents[group]
        return group

    def merge(left, right):
        left, right = root(left), root(right)
        if left != right:
            parents[max(left, right)] = min(left, right)

    used = 0
    # Equal projections across different candidates are not evidence of equal
    # tasks; collisions within any one candidate are sufficient to merge tasks.
    for model in models:
        seen: dict[str, str] = {}
        for index, item in enumerate(items):
            if deadline is not None and time.monotonic() >= deadline:
                raise InputGroupingViolation("NATIVE_INPUT_GROUPING_WALL_LIMIT")
            if check and index % 128 == 0:
                check()
            text = item["case"]["input"]
            if prepare_input:
                text = prepare_input(model, text)
            target = route_target(model, text) if model.provider == "router" else model
            try:
                rendered = target.request_contract.render(text) if target.request_contract else text
                encoded = json.dumps(
                    _normalized(rendered), sort_keys=True, ensure_ascii=False, allow_nan=False
                ).encode()
            except (ValueError, KeyError, TypeError, IndexError, RecursionError):
                raise InputGroupingViolation("NATIVE_INPUT_GROUPING_RENDER_FAILED") from None
            used += len(encoded)
            if used > MAX_PROJECTED_BYTES:
                raise InputGroupingViolation("NATIVE_INPUT_GROUPING_WORK_LIMIT")
            identity = hashlib.sha256(encoded).hexdigest()
            group = item["group_hash"]
            if identity in seen:
                merge(group, seen[identity])
            else:
                seen[identity] = group
    splits: dict[str, set[str]] = {}
    for item in items:
        splits.setdefault(root(item["group_hash"]), set()).add(item["split"])
    if any(len(values) > 1 for values in splits.values()):
        raise InputGroupingViolation("NATIVE_INPUT_FAMILY_SPANS_DATASET_SPLITS")
    selected = [item for item in items if item["split"] == plan.split]
    if plan.sample_limit:
        selected = selected[: plan.sample_limit]
    receipt = {
        "version": "native-input-groups-v1",
        "scope": "frozen_compressed_native_inputs_in_current_dataset"
        if plan.compression_execution
        else "frozen_text_request_templates_in_current_dataset",
        "cross_dataset_rendered_input_history": "unknown",
        "cross_split_check": "passed",
        "raw_groups": len(parents),
        "effective_groups": len(splits),
        "selected_effective_groups": len({root(item["group_hash"]) for item in selected}),
    }
    return {
        **dataset,
        "items": [{**item, "group_hash": root(item["group_hash"])} for item in items],
    }, receipt
