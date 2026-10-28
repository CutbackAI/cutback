"""Frozen metadata-only observation policies; no inference or deployment authority."""

from datetime import timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, StrictBool, StrictInt, model_validator

from cutback.core.improvement_economics import ImprovementAssumptions
from cutback.core.models import Identifier, StrictModel

Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class OutcomeWatchIntent(StrictModel):
    contract: Literal["cutback.outcome-watch.v1"] = "cutback.outcome-watch.v1"
    watch_id: UUID
    key: Identifier
    baseline_snapshot_id: UUID
    change_id: UUID
    release_linkage_attested: StrictBool
    candidate_release: Identifier
    window_seconds: Annotated[StrictInt, Field(ge=60, le=2592000)]
    interval_seconds: Annotated[StrictInt, Field(ge=60, le=2592000)]
    first_window_end: AwareDatetime
    grace_seconds: Annotated[StrictInt, Field(ge=0, le=86400)] = 300
    expires_at: AwareDatetime
    max_windows: Annotated[StrictInt, Field(ge=1, le=100)] = 30
    expected_tasks: Annotated[StrictInt, Field(ge=0, le=1000000)] | None = None
    assumptions: ImprovementAssumptions = Field(default_factory=ImprovementAssumptions)

    @model_validator(mode="after")
    def bounds(self):
        if not all(
            identifier.int
            for identifier in [
                self.watch_id,
                self.baseline_snapshot_id,
                self.change_id,
            ]
        ):
            raise ValueError("NONZERO_OUTCOME_WATCH_IDENTITIES_REQUIRED")
        if not self.release_linkage_attested:
            raise ValueError("EXPLICIT_RELEASE_LINKAGE_ATTESTATION_REQUIRED")
        if self.interval_seconds < self.window_seconds:
            raise ValueError("OUTCOME_WATCH_WINDOWS_CANNOT_OVERLAP")
        try:
            first_due = self.first_window_end + timedelta(seconds=self.grace_seconds)
            last_allowed = first_due + timedelta(days=30)
        except OverflowError:
            raise ValueError("BOUNDED_OUTCOME_WATCH_TIMES_REQUIRED") from None
        if not first_due < self.expires_at <= last_allowed:
            raise ValueError("OUTCOME_WATCH_REQUIRES_ORDERED_AT_MOST_30_DAY_LIFETIME")
        return self


class OutcomeWatchSpec(OutcomeWatchIntent):
    baseline_sha256: Sha256
    source_diff_sha256: Sha256
    optimization_run_ids: list[UUID] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def run_identities(self):
        if not all(identifier.int for identifier in self.optimization_run_ids):
            raise ValueError("NONZERO_OUTCOME_WATCH_RUNS_REQUIRED")
        if len(set(self.optimization_run_ids)) != len(self.optimization_run_ids):
            raise ValueError("DUPLICATE_OUTCOME_WATCH_RUNS")
        return self


class OutcomeWatchEnvelope(StrictModel):
    project_id: UUID
    environment_id: UUID
    policy: OutcomeWatchSpec


def due_window(spec, now, last_index):
    """Choose only the latest closed eligible window; never burst over missed ticks."""
    first_due = spec.first_window_end + timedelta(seconds=spec.grace_seconds)
    if now < first_due or now >= spec.expires_at:
        return None
    index = int((now - first_due).total_seconds()) // spec.interval_seconds
    if index <= last_index:
        return None
    end = spec.first_window_end + timedelta(seconds=index * spec.interval_seconds)
    return {
        "index": index,
        "start": end - timedelta(seconds=spec.window_seconds),
        "end": end,
        "next_due": end + timedelta(seconds=spec.interval_seconds + spec.grace_seconds),
        "skipped": max(0, index - last_index - 1),
    }


def outcome_review_action(report):
    reasons = set(report["reasons"])
    if reasons & {"insufficient_declared_task_sample", "outcome_coverage_unknown"}:
        return "COLLECT_MISSING_EVIDENCE"
    if report["descriptive_outcome_non_regression"] is False:
        return "REVIEW_ROLLBACK"
    if "no_observed_modeled_cost_improvement" in reasons:
        return "REVIEW_COST_REGRESSION"
    return "COLLECT_MISSING_EVIDENCE" if reasons else "PREPARE_INDEPENDENT_CONFIRMATION"
