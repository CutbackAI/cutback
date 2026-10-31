"""Pure conservative campaign compilation, before approval or physical requests."""

import json
from decimal import ROUND_DOWN, Context, Decimal, localcontext

from cutback.evals.campaigns import (
    PORTFOLIO_VERSIONS,
    SIMPLE_FIRST_VERSIONS,
    WORKLOAD_CAMPAIGN_VERSION,
    ImprovementCampaign,
    generated_validation_controls,
    generation_input_upper_bytes,
    generation_seeds,
    proposal_model,
    static_alternative_available,
    static_candidates,
)
from cutback.evals.experiments import upper_cost
from cutback.pricing import Catalog


def compile_campaign(campaign: ImprovementCampaign, dataset, baseline_cost):
    """Freeze conservative effort without reducing validation or confirmation samples."""
    # Cost compilation must not inherit an application's precision, rounding or traps.
    with localcontext(Context(prec=64)):
        if campaign.version not in PORTFOLIO_VERSIONS:
            return _compile_campaign(campaign, dataset, baseline_cost)
        alternatives = static_candidates(campaign)
        # Narrow complete alternatives before approval, never validation/test samples.
        # Keep the deterministic requested order and record all omitted choices.
        counts = range(len(alternatives), 0, -1) if alternatives else [0]
        first = None
        for count in counts:
            narrowed = campaign.model_copy(update={"static_portfolio": alternatives[:count]})
            receipt = _compile_campaign(narrowed, dataset, baseline_cost)
            selected = alternatives[:count]
            receipt.update(
                requested_static_count=len(alternatives),
                selected_static_keys=[m.key for m in selected],
                omitted_static_keys=[m.key for m in alternatives[count:]],
            )
            if receipt["eligible"]:
                return receipt
            if first is None:
                first = receipt
        return first


def _compile_campaign(campaign: ImprovementCampaign, dataset, baseline_cost):
    """Select bounded effort, retaining all validation and confirmation cases.

    Baseline cost must come from a scoped, integrity-checked retained snapshot.
    Future volume and unit savings remain owner assumptions, never measured ROI.
    This does not authorize inference or change an application.
    """
    plan, generator = campaign.template, campaign.generator
    simple_first = campaign.version in SIMPLE_FIRST_VERSIONS
    portfolio_version = campaign.version in PORTFOLIO_VERSIONS
    workload_version = campaign.version == WORKLOAD_CAMPAIGN_VERSION
    initial_stage = simple_first and static_alternative_available(campaign)
    if plan.price_sha256 != Catalog().sha256 or dataset["immutable_hash"] != plan.dataset_hash:
        raise ValueError("CAMPAIGN_DATASET_OR_PRICE_CONTRACT_CHANGED")
    counts = {
        split: sum(r["split"] == split for r in dataset["items"])
        for split in ("train", "validation", "test")
    }
    if not all(counts.values()):
        raise ValueError("CAMPAIGN_REQUIRES_TRAIN_VALIDATION_AND_TEST_GROUPS")
    alternatives = static_candidates(campaign)
    models = [plan.baseline, *alternatives]
    for model in models:
        options = json.dumps(model.request_contract.options) if model.request_contract else ""
        instruction_bytes = max(len(model.instruction.encode()), generator.max_instruction_bytes)
        for item in dataset["items"]:
            text = item["case"]["input"]
            if item["case"].get("episode") or not isinstance(text, str):
                raise ValueError("CAMPAIGN_REQUIRES_QUALIFIED_TEXT_CASES")
            rendered = model.request_contract.render(text) if model.request_contract else text
            wire = (
                json.dumps(rendered, ensure_ascii=False)
                if not isinstance(rendered, str)
                else rendered
            )
            if instruction_bytes + len((wire + options).encode()) + 4096 > model.max_input_tokens:
                raise ValueError("CAMPAIGN_TARGET_INPUT_BOUND_TOO_SMALL_FOR_PERMITTED_VARIANTS")
    base = upper_cost(plan.baseline)
    simple = max((upper_cost(m) for m in alternatives), default=base)
    generated = max(
        upper_cost(m.model_copy(update={"max_output_tokens": generator.max_output_tokens}))
        for m in generation_seeds(campaign)
    )
    proposer = upper_cost(proposal_model(generator))
    judge_calls = (2 if plan.judge_swap_orders else 1) if plan.judge else 0
    judge_cost = upper_cost(plan.judge) * judge_calls if plan.judge else Decimal(0)
    val_n, test_n = (counts[s] * plan.repeats for s in ("validation", "test"))
    confirmation_cost = test_n * (base + max(simple, generated) + judge_cost)
    confirmation_calls = test_n * (2 + judge_calls)
    initial_cost = (
        val_n
        * (
            base
            + sum((upper_cost(m) for m in alternatives), Decimal(0))
            + len(alternatives) * judge_cost
        )
        if initial_stage
        else Decimal(0)
    )
    initial_calls = val_n * (1 + len(alternatives) * (1 + judge_calls)) if initial_stage else 0
    reasons = []
    savings, volume = (
        campaign.conservative_savings_per_success_usd,
        campaign.forecast_successful_tasks,
    )
    if baseline_cost is None or savings is None or volume is None:
        reasons.append("complete_baseline_cost_or_conservative_opportunity_unknown")
    elif savings > baseline_cost:
        reasons.append("unit_savings_assumption_exceeds_observed_baseline_cost")
    with localcontext() as context:
        context.prec = 64
        gross = savings * volume if savings is not None and volume is not None else None
        cap = (
            min(campaign.budget.max_usd, gross * campaign.max_fraction_of_opportunity).quantize(
                Decimal("0.000000000001"), rounding=ROUND_DOWN
            )
            if gross is not None
            else None
        )
        choices = []
        if not reasons and gross is not None and gross > 0 and cap is not None:
            if (
                initial_stage
                and initial_cost + confirmation_cost <= cap
                and initial_calls + confirmation_calls <= campaign.budget.max_requests
            ):
                choices.append(
                    (
                        0,
                        0,
                        -(initial_cost + confirmation_cost),
                        0,
                        initial_calls + confirmation_calls,
                        initial_cost + confirmation_cost,
                    )
                )
            for rounds in range(1, generator.rounds + 1):
                for variants in range(1, generator.variants_per_round + 1):
                    controls = generated_validation_controls(campaign)
                    static_targets = len(controls)
                    control_cost = sum((upper_cost(m) for m in controls), Decimal(0))
                    val_cost = val_n * (
                        base
                        + control_cost
                        + variants * generated
                        + (static_targets + variants) * judge_cost
                    )
                    val_calls = val_n * (
                        1 + static_targets + variants + (static_targets + variants) * judge_calls
                    )
                    total = initial_cost + rounds * (proposer + val_cost) + confirmation_cost
                    requests = initial_calls + rounds * (1 + val_calls) + confirmation_calls
                    input_bound = generation_input_upper_bytes(
                        campaign, dataset["items"], rounds=rounds, variants=variants
                    )
                    if (
                        total <= cap
                        and requests <= campaign.budget.max_requests
                        and input_bound <= generator.model.max_input_tokens
                    ):
                        choices.append(
                            (rounds * variants, rounds, -total, variants, requests, total)
                        )
        if choices:
            _, rounds, _, variants, requests, total = max(choices)
        else:
            rounds = variants = requests = 0
            total = None
            if not reasons:
                reasons.append("complete_campaign_exceeds_opportunity_request_or_input_cap")
    return {
        "contract": "cutback.campaign-budget.v4"
        if workload_version
        else "cutback.campaign-budget.v3"
        if portfolio_version
        else "cutback.campaign-budget.v2"
        if simple_first
        else "cutback.campaign-budget.v1",
        "eligible": bool(choices),
        "rounds": rounds,
        "variants_per_round": variants,
        "effective_max_usd": str(cap) if cap is not None else None,
        "max_requests": requests,
        "generator_input_upper_bytes": (
            generation_input_upper_bytes(
                campaign, dataset["items"], rounds=rounds, variants=variants
            )
            if choices and rounds
            else None
        ),
        "worst_case_modeled_usd": str(total) if total is not None else None,
        "forecast_gross_modeled_opportunity_usd": str(gross) if gross is not None else None,
        "baseline_cost_per_success_usd": str(baseline_cost) if baseline_cost is not None else None,
        "static_baseline_kind": (
            "workload_qualified_native_and_exact_cache_portfolio"
            if workload_version
            else "native_model_and_output_cap_portfolio"
            if portfolio_version and alternatives
            else "unchanged_control_no_cheaper_static_variant"
            if portfolio_version
            else "model_alternative"
            if models[1].model != models[0].model
            else "output_cap_reduction"
            if models[1].max_output_tokens < models[0].max_output_tokens
            else "unchanged_control_no_cheaper_static_variant"
        ),
        "split_samples": counts,
        "reasons": reasons,
        "evidence": "retained_baseline_and_owner_forecast_not_measured_future_savings",
        "paid_calls_authorized": False,
        "provider_calls": 0,
        **(
            {
                "strategy_order": (
                    [
                        "workload_qualified_native_cache_portfolio"
                        if workload_version
                        else "static_native_portfolio"
                        if portfolio_version
                        else "static_native_alternative"
                    ]
                    if initial_stage
                    else []
                )
                + ["instruction_search_if_needed", "independent_confirmation"],
                "static_alternative_available": initial_stage,
                "initial_validation_requests": initial_calls,
                "instruction_search_enabled": bool(rounds),
                "optimization_overhead_scope": (
                    "native_and_declared_cache_operations_other_costs_unknown"
                    if workload_version
                    else "native_provider_calls_only_other_costs_not_measured"
                ),
            }
            if simple_first
            else {}
        ),
        **(
            {
                "static_portfolio": [
                    {
                        "key": m.key,
                        "provider": m.provider,
                        "model": m.model,
                        "max_output_tokens": m.max_output_tokens,
                        "worst_case_request_usd": str(upper_cost(m)),
                        **({"engine": "exact_cache"} if m.exact_cache else {}),
                    }
                    for m in alternatives
                ]
            }
            if portfolio_version
            else {}
        ),
    }
