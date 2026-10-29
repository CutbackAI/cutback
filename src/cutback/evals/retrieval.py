"""Frozen corpus retrieval experiments and bounded local preparation accounting."""

import time
from collections import defaultdict
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field, model_validator

from cutback.core.models import StrictModel
from cutback.core.retrieval import (
    CONTRACT_SHA256,
    FRAME_INSTRUCTION,
    LocalRetrievalIndex,
    RetrievalConfigurationBinding,
    RetrievalCorpus,
    RetrievalVariant,
)


def exact_decimal(value):
    if type(value) not in {Decimal, int, str}:
        raise ValueError("RETRIEVAL_EXACT_DECIMAL_ASSUMPTION_REQUIRED")
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise ValueError("RETRIEVAL_EXACT_DECIMAL_ASSUMPTION_REQUIRED") from None
    exponent = amount.as_tuple().exponent
    if not amount.is_finite() or not isinstance(exponent, int) or not -12 <= exponent <= 6:
        raise ValueError("RETRIEVAL_EXACT_DECIMAL_ASSUMPTION_REQUIRED")
    return amount


AssumedCPURate = Annotated[
    Decimal, BeforeValidator(exact_decimal), Field(ge=0, le=1000000, decimal_places=12)
]


class RetrievalExecutionSpec(StrictModel):
    version: Literal["cutback.retrieval-execution.v1"] = "cutback.retrieval-execution.v1"
    contract_sha256: str = Field(default=CONTRACT_SHA256, pattern=r"^[a-f0-9]{64}$")
    corpus: RetrievalCorpus
    variants: tuple[RetrievalVariant, ...] = Field(min_length=2, max_length=6)
    max_query_bytes: int = Field(default=1024, strict=True, ge=1, le=8192)
    # An explicit CPU-only cost assumption, never a measured infrastructure invoice.
    assumed_usd_per_cpu_hour: AssumedCPURate | None = None
    cpu_charge_quantum_ms: int = Field(default=1, strict=True, ge=1, le=1000)
    configuration_binding: RetrievalConfigurationBinding | None = None

    @model_validator(mode="after")
    def valid(self):
        if self.contract_sha256 != CONTRACT_SHA256:
            raise ValueError("RETRIEVAL_CONTRACT_CHANGED_REAPPROVAL_REQUIRED")
        keys = [item.model_key for item in self.variants]
        if len(set(keys)) != len(keys):
            raise ValueError("RETRIEVAL_VARIANT_MODEL_KEYS_MUST_BE_UNIQUE")
        if (
            self.configuration_binding
            and self.configuration_binding.settings != self.variants[0].settings
        ):
            raise ValueError("RETRIEVAL_BASELINE_DIFFERS_FROM_BOUND_CONFIGURATION")
        return self

    def input_byte_bound(self):
        # JSON control escaping expands one UTF-8 byte by at most six.
        return (
            self.max_query_bytes * 6
            + max(v.settings.max_context_bytes for v in self.variants)
            + len(FRAME_INSTRUCTION.encode())
            + 128
        )

    def public_summary(self):
        return {
            "version": self.version,
            "contract_sha256": self.contract_sha256,
            "documents": len(self.corpus.documents),
            "variants": [item.model_dump(mode="json") for item in self.variants],
            "max_query_bytes": self.max_query_bytes,
            "assumed_usd_per_cpu_hour": str(self.assumed_usd_per_cpu_hour)
            if self.assumed_usd_per_cpu_hour is not None
            else None,
            "cpu_charge_quantum_ms": self.cpu_charge_quantum_ms,
            "configuration_binding": self.configuration_binding.model_dump(mode="json")
            if self.configuration_binding
            else None,
            "content": "authorized_encrypted_plan_only",
            "cost_scope": "native_api_and_declared_cpu_components_not_task_or_invoice",
        }


class RetrievalProcessor:
    def __init__(self, spec: RetrievalExecutionSpec, *, check=None):
        self.spec = spec
        self.check = check
        self.settings = {item.model_key: item.settings for item in spec.variants}
        self.indexes: dict[str, LocalRetrievalIndex] = {}
        self.stats: defaultdict[str, dict[str, int]] = defaultdict(
            lambda: {
                "index_cpu_ns": 0,
                "query_cpu_ns": 0,
                "index_wall_ns": 0,
                "query_wall_ns": 0,
                "preparations": 0,
                "failed_preparations": 0,
                "context_bytes": 0,
            }
        )
        self.last: dict[str, Any] | None = None

    def _assumed_cost(self, cpu_ns):
        rate = self.spec.assumed_usd_per_cpu_hour
        if rate is None:
            return None
        with localcontext(Context(prec=80, rounding=ROUND_HALF_EVEN)):
            quantum = self.spec.cpu_charge_quantum_ms * 1000000
            charged = max(quantum, ((cpu_ns + quantum - 1) // quantum) * quantum)
            return (Decimal(charged) * rate / Decimal(3600000000000)).quantize(
                Decimal("0.000000000001"), rounding=ROUND_CEILING
            )

    def prepare(self, model, query):
        if self.check:
            self.check()
        if model.key not in self.settings:
            raise ValueError("RETRIEVAL_MODEL_VARIANT_NOT_APPROVED")
        self.last = None
        stats = self.stats[model.key]
        if model.key not in self.indexes:
            cpu, wall = time.thread_time_ns(), time.perf_counter_ns()
            try:
                self.indexes[model.key] = LocalRetrievalIndex(
                    self.spec.corpus, self.settings[model.key], check=self.check
                )
            finally:
                stats["index_cpu_ns"] += time.thread_time_ns() - cpu
                stats["index_wall_ns"] += time.perf_counter_ns() - wall
        cpu, wall = time.thread_time_ns(), time.perf_counter_ns()
        stats["preparations"] += 1
        try:
            result = self.indexes[model.key].query(
                query, max_query_bytes=self.spec.max_query_bytes, check=self.check
            )
            rendered = result.render(query)
            self.last = {
                "model_key": model.key,
                "contract_sha256": self.spec.contract_sha256,
                "settings": self.settings[model.key].model_dump(mode="json"),
                "documents": [p.wire() for p in result.passages],
                "passages": [
                    {
                        "document_id": str(p.document_id),
                        "start": p.start,
                        "end": p.end,
                        "score": str(p.score),
                    }
                    for p in result.passages
                ],
                "context_bytes": result.context_bytes,
                "rendered_input_bytes": len(rendered.encode()),
                "scored_chunks": result.scored_chunks,
            }
            stats["context_bytes"] += result.context_bytes
            return rendered
        except Exception:
            stats["failed_preparations"] += 1
            raise
        finally:
            elapsed_cpu, elapsed_wall = time.thread_time_ns() - cpu, time.perf_counter_ns() - wall
            stats["query_cpu_ns"] += elapsed_cpu
            stats["query_wall_ns"] += elapsed_wall
            if self.last is not None:
                self.last.update(cpu_ns=elapsed_cpu, wall_ns=elapsed_wall)

    def finalize(self, report):
        with localcontext(Context(prec=80, rounding=ROUND_HALF_EVEN)):
            return self._finalize(report)

    def _finalize(self, report):
        totals: dict[str, dict[str, Any]] = {}
        for key, stats in self.stats.items():
            cpu_cost = self._assumed_cost(stats["index_cpu_ns"] + stats["query_cpu_ns"])
            model = next((r for r in report.get("models", []) if r["model"]["key"] == key), None)
            combined = None
            if model is not None and model["cost_complete"] and cpu_cost is not None:
                combined = Decimal(model["known_cost_subtotal_usd"]) + cpu_cost
            totals[key] = {
                **stats,
                "assumed_cpu_cost_usd": str(cpu_cost) if cpu_cost is not None else None,
                "modeled_component_cost_usd": str(combined) if combined is not None else None,
            }
        baseline = (report.get("models") or [{}])[0].get("model", {}).get("key")
        base_raw = totals.get(baseline, {}).get("modeled_component_cost_usd")
        base = Decimal(base_raw) if isinstance(base_raw, str) else None
        for recommendation in report.get("recommendations", []):
            recommendation["retrieval_quality_assessment_passed"] = recommendation[
                "quality_gate_passed"
            ]
            candidate_raw = totals.get(recommendation["candidate"]["key"], {}).get(
                "modeled_component_cost_usd"
            )
            candidate = Decimal(candidate_raw) if isinstance(candidate_raw, str) else None
            known = base is not None and candidate is not None
            recommendation["sample_inference_cost_difference_usd"] = recommendation[
                "sample_modeled_cost_difference_usd"
            ]
            recommendation["sample_modeled_cost_difference_usd"] = (
                str(base - candidate) if base is not None and candidate is not None else None
            )
            recommendation["cheaper_on_sample"] = (
                candidate < base if base is not None and candidate is not None else None
            )
            recommendation["retrieval_automation_eligible"] = bool(
                base is not None
                and candidate is not None
                and recommendation["quality_gate_passed"]
                and recommendation["evidence_tier"] == 4
                and candidate < base
            )
            recommendation["economics_scope"] = (
                "native_api_plus_declared_cpu_components_not_full_task_or_invoice"
            )
            if not known:
                recommendation.update(
                    status="NEEDS_VALIDATION",
                    quality_gate_passed=False,
                    decision_reason="retrieval_cpu_cost_assumption_or_native_usage_unknown",
                    next_action="declare_cpu_cost_basis_and_review_incomplete_usage",
                )
        report["retrieval"] = {
            **self.spec.public_summary(),
            "models": totals,
            "full_task_cost_known": False,
            "one_time_index_cost_included_in_sample_components": True,
        }
        report["recommendations"] = sorted(
            report.get("recommendations", []),
            key=lambda r: Decimal(r.get("sample_modeled_cost_difference_usd") or "-Infinity"),
            reverse=True,
        )
        return report
