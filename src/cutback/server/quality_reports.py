"""Bounded observed-cohort comparisons; no inferred population or causal claims."""

from collections import Counter
from datetime import timedelta
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, model_validator
from sqlalchemy import select

from cutback.core.models import StrictModel
from cutback.server.quality_db import QualityEvaluation


class QualityComparison(StrictModel):
    baseline_start: AwareDatetime
    baseline_end: AwareDatetime
    current_start: AwareDatetime
    current_end: AwareDatetime
    group_by: Literal["release", "service", "input_size_bucket"] = "release"

    @model_validator(mode="after")
    def windows(self):
        if not (self.baseline_start < self.baseline_end <= self.current_start < self.current_end):
            raise ValueError("ORDERED_NONOVERLAPPING_COHORT_WINDOWS_REQUIRED")
        if self.current_end - self.baseline_start > timedelta(days=30):
            raise ValueError("COHORT_COMPARISON_MAXIMUM_30_DAYS")
        return self


def summarize(rows, dimension):
    def counts(values):
        states = Counter(r.state for r in values)
        decided = states["passed"] + states["failed"]
        known = [r.spent_usd for r in values if r.spent_usd is not None]
        return {
            "retained_selected": len(values),
            "states": dict(sorted(states.items())),
            "decided": decided,
            "failure_fraction_of_decided": str(Decimal(states["failed"]) / decided)
            if decided
            else None,
            "known_spent_usd": str(sum(known, Decimal(0))),
            "cost_unknown_receipts": sum(r.charge_started and r.spent_usd is None for r in values),
        }

    groups: dict[str | None, list[QualityEvaluation]] = {}
    for row in rows:
        key = row.source_metadata.get(dimension)
        # Values originate from validated span metadata or fixed coarse feature enums.
        # Null remains distinct from the literal identifier "unknown".
        groups.setdefault(key, []).append(row)
    if len(groups) > 100:
        return {**counts(rows), "groups": [], "groups_truncated": True}
    return {
        **counts(rows),
        "groups": [
            {"value": key, **counts(values)}
            for key, values in sorted(
                groups.items(), key=lambda pair: (pair[0] is not None, pair[0] or "")
            )
        ],
        "groups_truncated": False,
    }


def compare_quality(session, rule, body):
    # The queue's 50k/project cap bounds memory even before these explicit limits.
    def cohort(start, end):
        rows = session.scalars(
            select(QualityEvaluation)
            .where(
                QualityEvaluation.project_id == rule.project_id,
                QualityEvaluation.rule_id == rule.id,
                QualityEvaluation.occurred_at >= start,
                QualityEvaluation.occurred_at < end,
            )
            .order_by(QualityEvaluation.id)
            .limit(50001)
        ).all()
        if len(rows) > 50000:
            raise ValueError("QUALITY_COHORT_EXCEEDS_RECEIPT_BOUND")
        return summarize(rows, body.group_by)

    baseline = cohort(body.baseline_start, body.baseline_end)
    current = cohort(body.current_start, body.current_end)
    first, second = (r["failure_fraction_of_decided"] for r in (baseline, current))
    # Descriptive, no independent reference labels or representative sampling assumed.
    delta = (
        str(Decimal(second) - Decimal(first)) if first is not None and second is not None else None
    )
    distribution = None
    if not baseline["groups_truncated"] and not current["groups_truncated"]:
        a = {
            r["value"]: r["retained_selected"] for r in baseline["groups"] if r["value"] is not None
        }
        b = {
            r["value"]: r["retained_selected"] for r in current["groups"] if r["value"] is not None
        }
        if sum(a.values()) and sum(b.values()):
            distribution = str(
                sum(
                    (
                        abs(
                            Decimal(a.get(k, 0)) / sum(a.values())
                            - Decimal(b.get(k, 0)) / sum(b.values())
                        )
                        for k in a.keys() | b.keys()
                    ),
                    Decimal(0),
                )
                / 2
            )
    return {
        "rule_id": str(rule.id),
        "evaluator": rule.specification["evaluator"],
        "windows": body.model_dump(mode="json"),
        "baseline": baseline,
        "current": current,
        "failure_fraction_delta": delta,
        "known_category_total_variation": distribution,
        "interpretation": "descriptive_observed_shift_not_significance_or_causation",
        "population_coverage": "unknown",
        "independent_task_success": "not_verified",
        "input_features": "authorized_coarse_size_only"
        if rule.specification.get("record_input_features")
        else "not_authorized_or_observed",
        "retention": "missing_or_deleted_receipts_not_reconstructed",
    }
