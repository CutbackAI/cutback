"""Conservative hypotheses derived only from source-linked or observed evidence."""

from decimal import Decimal


def static_opportunities(inventory):
    from cutback.pricing import Catalog

    catalog = Catalog()
    findings = []
    for site in inventory["call_sites"]:
        common = {
            "id": site["call_site_id"],
            "source": site["evidence"][0],
            "evidence_status": "discovered",
            "evidence_tier": 0,
            "quality_status": "not_evaluated",
            "expected_savings_usd": None,
            "baseline_model": site["requested_model"],
        }
        rate = catalog.rate_info(site["provider"], site["requested_model"])["rate"]
        if rate:
            cheaper = [
                r
                for r in catalog.data["rates"]
                if r["provider"] == site["provider"]
                and Decimal(r["input"]) < Decimal(rate["input"])
                and Decimal(r["output"]) <= Decimal(rate["output"])
            ]
            if cheaper:
                findings.append(
                    {
                        **common,
                        "strategy": "evaluate_model_swap",
                        "candidates": [r["model"] for r in cheaper[:3]],
                        "pricing_snapshot": catalog.snapshot_id,
                        "reason": "lower_public_text_rates_do_not_establish_task_compatibility",
                        "next_action": "freeze_authorized_examples_and_predeclare_quality_gate",
                    }
                )
        if any((p.get("static_characters") or 0) >= 4000 for p in site["prompt_template_refs"]):
            findings.append(
                {
                    **common,
                    "strategy": "inspect_prompt_cache",
                    "reason": "long_static_text_in_source_not_measured_input_usage",
                    "next_action": "inspect_supported_cache_policy_and_observe_cache_token_usage",
                }
            )
        if (site.get("configured_max_output_tokens") or 0) >= 8192:
            findings.append(
                {
                    **common,
                    "strategy": "review_output_cap",
                    "reason": "large_configured_limit_is_a_risk_signal_not_billed_tokens",
                    "next_action": "observe_actual_output_lengths_and_truncation_before_changing",
                }
            )
        findings.append(
            {
                **common,
                "strategy": "measure_and_evaluate",
                "reason": "source_alone_has_no_historical_volume_or_quality_evidence",
                "next_action": "observe_runtime_then_import_authorized_representative_examples",
            }
        )
    return {
        "schema_version": "1.0",
        "opportunities": findings,
        "evidence_status": "discovered",
        "quality_status": "not_evaluated",
        "expected_savings_usd": None,
    }


def observed_opportunities(report: dict) -> dict:
    findings = []
    for group in report["groups"]:
        count = group["observed_attempts"]
        if not count:
            continue
        evidence = {"period": report["period"], "group": group, "group_by": report["group_by"]}
        common = {
            "target": group["key"],
            "evidence": evidence,
            "evidence_tier": 0,
            "quality_status": "NOT_EVALUATED",
            "expected_savings_usd": None,
            "approval_status": "not_requested",
        }
        if group["errors"] or (
            group["logical_operations"] is not None
            and group["observed_attempts"] > group["logical_operations"]
        ):
            findings.append(
                {
                    **common,
                    "strategy": "reduce_retries",
                    "reason": "observed_errors_or_repeated_attempts",
                    "next_action": "inspect_failure_classes_and_idempotency",
                }
            )
        if group["unpriced_requests"]:
            findings.append(
                {
                    **common,
                    "strategy": "resolve_pricing",
                    "reason": "cost_unknown_for_some_observed_attempts",
                    "next_action": "approve_an_effective_dated_rate_if_known",
                }
            )
        if group["priced_requests"]:
            findings.append(
                {
                    **common,
                    "strategy": "evaluate_model_swap",
                    "reason": "observed_modeled_cost_requires_quality_evaluation",
                    "next_action": "import_authorized_heldout_examples_and_plan",
                }
            )
    findings.sort(
        key=lambda f: Decimal(f["evidence"]["group"]["known_modeled_cost_subtotal_usd"] or "0"),
        reverse=True,
    )
    return {
        "schema_version": "1.0",
        "opportunities": findings,
        "ranking": "known_modeled_cost_subtotal_descending_not_verified_savings",
        "coverage": {
            **report["coverage"],
            "source_groups_truncated": report.get("groups_truncated", False),
        },
        "semantic_quality": "NOT VERIFIED",
    }
