"""Opaque reference to an immutable, reviewed evaluation-criteria version."""

from decimal import Decimal, InvalidOperation
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from cutback.core.models import StrictModel


class ReviewedCriteriaBinding(StrictModel):
    version: Literal["cutback.reviewed-criteria.v1"] = "cutback.reviewed-criteria.v1"
    criteria_id: UUID
    review_approval_id: UUID
    version_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def nonzero(self):
        if not self.criteria_id.int or not self.review_approval_id.int:
            raise ValueError("REVIEWED_CRITERIA_VERSION_ID_REQUIRED")
        return self


def source_evaluator_eligible(evidence):
    """Validate receipt structure locally; servers must recheck canonical authority."""
    if evidence.get("evaluator_version") == "deterministic-v3":
        return True
    if evidence.get("evaluator_version") != "deterministic-v13":
        return False
    try:
        ReviewedCriteriaBinding.model_validate(evidence.get("criteria_binding"))
        cost = evidence["criteria_generation_cost"]
        amount = Decimal(cost["known_spend_subtotal_usd"])
        return bool(
            evidence.get("criteria_review_assurance")
            == "owner_review_not_independent_judge_calibration"
            and UUID(evidence["criteria_confirmation_predecessor_run_id"]).int
            and UUID(cost["generation_run_id"]).int
            and cost["billing_complete"] is True
            and cost["shared_one_time_setup"] is True
            and cost["included_in_target_evaluation_spend"] is False
            and cost["amortized_per_task_usd"] is None
            and cost["full_task_economics_known"] is False
            and amount.is_finite()
            and amount >= 0
        )
    except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation):
        return False
