"""A retained source window can qualify a hypothesis, never authorize execution."""

from typing import Literal

from pydantic import model_validator

from cutback.core.cache_workload import CacheWorkloadAssessment
from cutback.core.exact_cache import ExactCacheExecution
from cutback.core.models import StrictModel
from cutback.core.request_profiles import fingerprint

WORKLOAD_PORTFOLIO_CONTRACT: Literal["workload-native-portfolio-v1"] = (
    "workload-native-portfolio-v1"
)


class WorkloadPortfolioBinding(StrictModel):
    version: Literal["workload-native-portfolio-v1"] = WORKLOAD_PORTFOLIO_CONTRACT
    assessment: CacheWorkloadAssessment
    cache_execution: ExactCacheExecution

    @model_validator(mode="after")
    def qualified(self):
        if (
            self.assessment.decision != "prepare_separate_cache_trial"
            or self.assessment.cache_execution_sha256
            != fingerprint(self.cache_execution.model_dump(mode="json"))
        ):
            raise ValueError("WORKLOAD_PORTFOLIO_REQUIRES_POSITIVE_EXACT_CACHE_ASSESSMENT")
        return self

    def validate_plan(self, plan):
        cached = [candidate for candidate in plan.candidates if candidate.exact_cache is not None]
        if (
            plan.evaluator_version != "deterministic-v3"
            or plan.source_binding is None
            or plan.source_binding.source_commit is None
            or plan.workflow != self.assessment.workflow
            or plan.baseline.provider != "openai"
            or plan.baseline.model != self.assessment.baseline_model
            or fingerprint(plan.source_binding.model_dump(mode="json"))
            != self.assessment.source_binding_sha256
            or any(candidate.exact_cache != self.cache_execution for candidate in cached)
            or not cached
            and not plan.predecessor_run_id
            or plan.split not in {"validation", "test"}
            or plan.staged
        ):
            raise ValueError("WORKLOAD_PORTFOLIO_SOURCE_BASELINE_OR_CACHE_CHANGED")
