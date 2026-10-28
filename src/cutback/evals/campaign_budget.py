"""Pure conservative campaign compilation, before approval or physical requests."""

import json
from decimal import ROUND_DOWN, Decimal, localcontext

from cutback.evals.campaigns import (
    ImprovementCampaign,
    generation_input_upper_bytes,
    proposal_model,
)
from cutback.evals.experiments import upper_cost
from cutback.pricing import Catalog


def compile_campaign(campaign: ImprovementCampaign, dataset, baseline_cost):
    """Select bounded effort, retaining all validation and confirmation cases.

    Baseline cost must come from a scoped, integrity-checked retained snapshot.
    Future volume and unit savings remain owner assumptions, never measured ROI.
    This does not authorize inference or change an application.
    """
    plan, generator = campaign.template, campaign.generator
    if plan.price_sha256 != Catalog().sha256 or dataset["immutable_hash"] != plan.dataset_hash:
        raise ValueError("CAMPAIGN_DATASET_OR_PRICE_CONTRACT_CHANGED")
    counts = {
        split: sum(r["split"] == split for r in dataset["items"])
        for split in ("train", "validation", "test")
    }
    if not all(counts.values()):
        raise ValueError("CAMPAIGN_REQUIRES_TRAIN_VALIDATION_AND_TEST_GROUPS")
    models = [plan.baseline, *plan.candidates]
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
    base, simple = (upper_cost(m) for m in models)
    generated = max(
        upper_cost(m.model_copy(update={"max_output_tokens": generator.max_output_tokens}))
        for m in models
    )
    proposer = upper_cost(proposal_model(generator))
    judge_calls = (2 if plan.judge_swap_orders else 1) if plan.judge else 0
    judge_cost = upper_cost(plan.judge) * judge_calls if plan.judge else Decimal(0)
    val_n, test_n = (counts[s] * plan.repeats for s in ("validation", "test"))
    confirmation_cost = test_n * (base + max(simple, generated) + judge_cost)
    confirmation_calls = test_n * (2 + judge_calls)
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
            for rounds in range(1, generator.rounds + 1):
                for variants in range(1, generator.variants_per_round + 1):
                    val_cost = val_n * (
                        base + simple + variants * generated + (1 + variants) * judge_cost
                    )
                    val_calls = val_n * (2 + variants + (1 + variants) * judge_calls)
                    total = rounds * (proposer + val_cost) + confirmation_cost
                    requests = rounds * (1 + val_calls) + confirmation_calls
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
        "contract": "cutback.campaign-budget.v1",
        "eligible": bool(choices),
        "rounds": rounds,
        "variants_per_round": variants,
        "effective_max_usd": str(cap) if cap is not None else None,
        "max_requests": requests,
        "generator_input_upper_bytes": (
            generation_input_upper_bytes(
                campaign, dataset["items"], rounds=rounds, variants=variants
            )
            if choices
            else None
        ),
        "worst_case_modeled_usd": str(total) if total is not None else None,
        "forecast_gross_modeled_opportunity_usd": str(gross) if gross is not None else None,
        "baseline_cost_per_success_usd": str(baseline_cost) if baseline_cost is not None else None,
        "static_baseline_kind": (
            "model_alternative"
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
    }
