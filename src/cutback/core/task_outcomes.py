"""Declared application outcomes and modeled costs, independent from HTTP success.

An external producer's completeness assertion is evidence about its own task,
not an independent billing audit or a representative customer-quality claim.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, model_validator

from cutback.core.models import Identifier, StrictModel
from cutback.core.traces import TraceID

ComponentMoney = Annotated[Decimal, Field(ge=0, max_digits=24, decimal_places=12)]
Count = Annotated[StrictInt, Field(ge=0, le=1000000)]


class TaskCostComponent(StrictModel):
    key: Identifier
    kind: Literal["tool", "retrieval", "compute", "hosting", "other"]
    amount_usd: ComponentMoney | None = None
    currency: Literal["USD"] = "USD"


class TaskOutcomeInput(StrictModel):
    schema_version: Literal["cutback.task-outcome.v1"] = "cutback.task-outcome.v1"
    task_id: UUID
    result_id: UUID
    expected_previous_revision: Annotated[StrictInt, Field(ge=0, le=99)] = 0
    environment: Identifier
    trace_id: TraceID
    workflow: Identifier
    service: Identifier
    release: Identifier | None = None
    occurred_at: datetime
    outcome: Literal["success", "failure", "unknown", "error"]
    source: Identifier
    source_version: Identifier
    basis: Literal["business_event", "human_review", "deterministic_evaluation", "model_assessment"]
    expected_native_attempts: Count | None = None
    other_costs_declared_complete: StrictBool = False
    components: list[TaskCostComponent] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def valid(self):
        if not self.task_id.int or not self.result_id.int or not int(self.trace_id, 16):
            raise ValueError("NONZERO_TASK_IDENTITIES_REQUIRED")
        if not self.occurred_at.tzinfo:
            raise ValueError("TASK_TIME_REQUIRES_OFFSET")
        if len({item.key for item in self.components}) != len(self.components):
            raise ValueError("TASK_COST_COMPONENT_KEYS_MUST_BE_UNIQUE")
        return self


class TaskOutcomeBatch(StrictModel):
    items: list[TaskOutcomeInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def fresh(self):
        now = datetime.now(UTC)
        if any(
            not now - timedelta(days=90) <= item.occurred_at <= now + timedelta(minutes=5)
            for item in self.items
        ):
            raise ValueError("TASK_OUTSIDE_INGEST_WINDOW")
        if len({item.result_id for item in self.items}) != len(self.items):
            raise ValueError("DUPLICATE_TASK_RESULT_IDENTITIES_IN_BATCH")
        return self


class NativeTaskCostEvidence(StrictModel):
    observed_attempts: Count
    priced_attempts: Count
    known_modeled_subtotal_usd: Decimal | None = Field(
        default=None, ge=0, max_digits=36, decimal_places=12
    )
    excluded_non_application_attempts: Count = 0
    trace_retained: StrictBool = False
    trace_integrity_complete: StrictBool = False

    @model_validator(mode="after")
    def coverage(self):
        if self.priced_attempts > self.observed_attempts:
            raise ValueError("PRICED_TASK_ATTEMPTS_EXCEED_OBSERVED_ATTEMPTS")
        if (self.priced_attempts > 0) != (self.known_modeled_subtotal_usd is not None):
            raise ValueError("TASK_KNOWN_SUBTOTAL_REQUIRES_PRICED_ATTEMPTS")
        if self.trace_integrity_complete and not self.trace_retained:
            raise ValueError("TASK_TRACE_INTEGRITY_REQUIRES_RETAINED_TRACE")
        return self


class TaskCostReport(StrictModel):
    task_id: UUID
    outcome: Literal["success", "failure", "unknown", "error"]
    outcome_evidence: str
    outcome_basis: str
    outcome_source_version: str
    observed_native_attempts: int
    priced_native_attempts: int
    expected_native_attempts: int | None
    native_attempt_count_matches_declaration: bool | None
    excluded_non_application_attempts: int
    known_native_modeled_subtotal_usd: str | None
    known_other_asserted_subtotal_usd: str | None
    unknown_declared_components: int
    declared_cost_complete: bool
    declared_modeled_total_usd: str | None
    cost_evidence: str
    billing_verification: str
    population_coverage: str


class TaskEconomicsSummary(StrictModel):
    submitted_tasks: int
    declared_successes: int
    declared_failures: int
    unknown_or_error_outcomes: int
    declared_cost_complete_tasks: int
    expected_tasks: int | None
    task_count_matches_declaration: bool | None
    declared_sample_total_usd: str | None
    known_declared_complete_task_subtotal_usd: str | None
    known_failed_task_native_modeled_subtotal_usd: str | None
    failed_tasks_with_known_native_cost: int
    declared_sample_cost_per_success_usd: str | None
    window_cost_complete_under_declaration: bool
    declared_window_cost_per_success_usd: str | None
    population_coverage: str
    billing_verification: str
    production_savings_usd: None = None


def task_economics(task: TaskOutcomeInput, native: NativeTaskCostEvidence) -> dict:
    count_matches = (
        native.observed_attempts == task.expected_native_attempts
        if task.expected_native_attempts is not None
        else None
    )
    native_complete = bool(
        native.trace_retained
        and native.trace_integrity_complete
        and count_matches is True
        and native.excluded_non_application_attempts == 0
        and native.observed_attempts == native.priced_attempts
    )
    components_complete = task.other_costs_declared_complete and all(
        c.amount_usd is not None for c in task.components
    )
    with localcontext() as context:
        context.prec = 64
        known_other = sum(
            (c.amount_usd for c in task.components if c.amount_usd is not None), Decimal(0)
        )
        native_total = (
            (native.known_modeled_subtotal_usd if native.observed_attempts else Decimal(0))
            if native_complete
            else None
        )
        total = (
            native_total + known_other if native_total is not None and components_complete else None
        )
    return {
        "task_id": str(task.task_id),
        "outcome": task.outcome,
        "outcome_evidence": "externally_asserted_not_independently_verified",
        "outcome_basis": task.basis,
        "outcome_source_version": task.source_version,
        "observed_native_attempts": native.observed_attempts,
        "priced_native_attempts": native.priced_attempts,
        "expected_native_attempts": task.expected_native_attempts,
        "native_attempt_count_matches_declaration": count_matches,
        "excluded_non_application_attempts": native.excluded_non_application_attempts,
        "known_native_modeled_subtotal_usd": str(native.known_modeled_subtotal_usd)
        if native.known_modeled_subtotal_usd is not None
        else None,
        "known_other_asserted_subtotal_usd": str(known_other)
        if any(c.amount_usd is not None for c in task.components)
        or (task.other_costs_declared_complete and not task.components)
        else None,
        "unknown_declared_components": sum(c.amount_usd is None for c in task.components),
        "declared_cost_complete": total is not None,
        "declared_modeled_total_usd": str(total) if total is not None else None,
        "cost_evidence": "modeled_native_rates_plus_externally_asserted_other_costs",
        "billing_verification": "not_independently_verified",
        "population_coverage": "unknown",
    }


def summarize_task_economics(rows: list[dict], *, expected_tasks: int | None = None) -> dict:
    if len(rows) > 10000 or (
        expected_tasks is not None
        and (type(expected_tasks) is not int or not 0 <= expected_tasks <= 1000000)
    ):
        raise ValueError("TASK_ECONOMICS_WINDOW_BOUND")
    if len({row["task_id"] for row in rows}) != len(rows):
        raise ValueError("TASK_ECONOMICS_CANNOT_DOUBLE_COUNT_A_TASK")
    success = sum(row["outcome"] == "success" for row in rows)
    failure = sum(row["outcome"] == "failure" for row in rows)
    unknown = len(rows) - success - failure
    complete = bool(rows and not unknown and all(row["declared_cost_complete"] for row in rows))
    window_complete = complete and expected_tasks is not None and len(rows) == expected_tasks
    failed_known_tasks = sum(
        row["outcome"] == "failure" and row["known_native_modeled_subtotal_usd"] is not None
        for row in rows
    )
    with localcontext() as context:
        context.prec = 64
        total = sum(
            (
                Decimal(row["declared_modeled_total_usd"])
                for row in rows
                if row["declared_cost_complete"]
            ),
            Decimal(0),
        )
        failed_known = sum(
            (
                Decimal(row["known_native_modeled_subtotal_usd"])
                for row in rows
                if row["outcome"] == "failure"
                and row["known_native_modeled_subtotal_usd"] is not None
            ),
            Decimal(0),
        )
        per_success = total / success if complete and success else None
    return {
        "submitted_tasks": len(rows),
        "declared_successes": success,
        "declared_failures": failure,
        "unknown_or_error_outcomes": unknown,
        "declared_cost_complete_tasks": sum(row["declared_cost_complete"] for row in rows),
        "expected_tasks": expected_tasks,
        "task_count_matches_declaration": len(rows) == expected_tasks
        if expected_tasks is not None
        else None,
        "declared_sample_total_usd": str(total) if complete else None,
        "known_declared_complete_task_subtotal_usd": str(total)
        if any(row["declared_cost_complete"] for row in rows)
        else None,
        "known_failed_task_native_modeled_subtotal_usd": str(failed_known)
        if failed_known_tasks
        else None,
        "failed_tasks_with_known_native_cost": failed_known_tasks,
        "declared_sample_cost_per_success_usd": str(per_success)
        if per_success is not None
        else None,
        "window_cost_complete_under_declaration": window_complete,
        "declared_window_cost_per_success_usd": str(per_success)
        if per_success is not None and window_complete
        else None,
        "population_coverage": "unknown",
        "billing_verification": "not_independently_verified",
        "production_savings_usd": None,
    }
