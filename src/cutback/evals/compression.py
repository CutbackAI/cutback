"""Frozen context-compression execution and measured local CPU components."""

import time
from collections import defaultdict
from decimal import ROUND_CEILING, Context, Decimal, localcontext
from typing import Any, Literal

from pydantic import Field, model_validator

from cutback.core.compression import (
    CONTRACT_SHA256,
    FRAME_INSTRUCTION,
    CompressionConfigurationBinding,
    CompressionVariant,
    ContextInput,
    compress_context,
)
from cutback.core.context_producer import ContextProducerBinding
from cutback.core.models import StrictModel
from cutback.evals.retrieval import AssumedCPURate


class CompressionExecutionSpec(StrictModel):
    version: Literal["cutback.compression-execution.v1"] = "cutback.compression-execution.v1"
    # Preserve runtime-specific reapproval without baking it into generated API artifacts.
    contract_sha256: str = Field(default_factory=lambda: CONTRACT_SHA256, pattern=r"^[a-f0-9]{64}$")
    variants: tuple[CompressionVariant, ...] = Field(min_length=2, max_length=6)
    max_query_bytes: int = Field(default=1024, strict=True, ge=1, le=4096)
    assumed_usd_per_cpu_hour: AssumedCPURate | None = None
    cpu_charge_quantum_ms: int = Field(default=1, strict=True, ge=1, le=1000)
    configuration_binding: CompressionConfigurationBinding | None = None

    @model_validator(mode="after")
    def valid(self):
        if self.contract_sha256 != CONTRACT_SHA256:
            raise ValueError("COMPRESSION_CONTRACT_CHANGED_REAPPROVAL_REQUIRED")
        if len({v.model_key for v in self.variants}) != len(self.variants):
            raise ValueError("COMPRESSION_VARIANTS_MUST_HAVE_UNIQUE_MODEL_KEYS")
        if (
            self.configuration_binding
            and self.configuration_binding.settings != self.variants[0].settings
        ):
            raise ValueError("COMPRESSION_BASELINE_DIFFERS_FROM_BOUND_CONFIGURATION")
        return self

    def input_byte_bound(self):
        return (
            (self.max_query_bytes + max(v.settings.max_context_bytes for v in self.variants)) * 6
            + len(FRAME_INSTRUCTION.encode())
            + 128
        )

    def public_summary(self):
        return {
            **self.model_dump(mode="json"),
            "content": "query_and_context_in_authorized_encrypted_dataset_only",
            "cost_scope": "native_api_and_declared_cpu_components_not_task_or_invoice",
        }


class JointModelContextExecutionSpec(StrictModel):
    """Separate approval for a frozen native model/output/context search.

    The legacy fields remain independently declared so their serialized order
    and contract do not change when this execution target evolves.
    """

    version: Literal["cutback.joint-model-context.v1"] = "cutback.joint-model-context.v1"
    transport: Literal["native_provider", "scripted_test_only"] = "native_provider"
    contract_sha256: str = Field(default_factory=lambda: CONTRACT_SHA256, pattern=r"^[a-f0-9]{64}$")
    variants: tuple[CompressionVariant, ...] = Field(min_length=2, max_length=6)
    max_query_bytes: int = Field(default=1024, strict=True, ge=1, le=4096)
    assumed_usd_per_cpu_hour: AssumedCPURate | None = None
    cpu_charge_quantum_ms: int = Field(default=1, strict=True, ge=1, le=1000)
    configuration_binding: CompressionConfigurationBinding | None = None
    producer_binding: ContextProducerBinding | None = None

    @model_validator(mode="after")
    def valid(self):
        if self.producer_binding and (
            not self.configuration_binding
            or self.producer_binding.commit != self.configuration_binding.commit
            or self.producer_binding.source_tree_hash != self.configuration_binding.source_tree_hash
        ):
            raise ValueError("JOINT_PRODUCER_AND_SETTINGS_MUST_BIND_SAME_SOURCE_TREE")
        if self.producer_binding:
            from pathlib import PurePosixPath

            assert self.configuration_binding is not None
            expected = str(PurePosixPath(self.producer_binding.file).parent / "compression.json")
            if self.configuration_binding.file != expected:
                raise ValueError("JOINT_SETTINGS_MUST_BE_ADJACENT_TO_QUALIFIED_PRODUCER")
        if self.contract_sha256 != CONTRACT_SHA256:
            raise ValueError("COMPRESSION_CONTRACT_CHANGED_REAPPROVAL_REQUIRED")
        if len({value.model_key for value in self.variants}) != len(self.variants):
            raise ValueError("COMPRESSION_VARIANTS_MUST_HAVE_UNIQUE_MODEL_KEYS")
        if (
            self.configuration_binding
            and self.configuration_binding.settings != self.variants[0].settings
        ):
            raise ValueError("COMPRESSION_BASELINE_DIFFERS_FROM_BOUND_CONFIGURATION")
        return self

    def input_byte_bound(self):
        return (
            (
                self.max_query_bytes
                + max(value.settings.max_context_bytes for value in self.variants)
            )
            * 6
            + len(FRAME_INSTRUCTION.encode())
            + 128
        )

    def public_summary(self):
        return {
            **self.model_dump(mode="json"),
            "search_axes": ["model", "output_cap", "extractive_context"],
            "content": "query_and_context_in_authorized_encrypted_dataset_only",
            "cost_scope": "native_api_and_declared_cpu_components_not_task_or_invoice",
        }


class CompressionProcessor:
    """A bounded RAM-only cache also accounts for lineage preprocessing CPU."""

    def __init__(
        self,
        spec: CompressionExecutionSpec | JointModelContextExecutionSpec,
        *,
        check=None,
        deadline=None,
        split=None,
    ):
        self.spec, self.check, self.deadline, self.split = spec, check, deadline, split
        self.settings = {v.model_key: v.settings for v in spec.variants}
        self.cache: dict[tuple[str, str], tuple[str, dict]] = {}
        self.cache_bytes = 0
        self.stats: defaultdict[str, dict[str, int]] = defaultdict(
            lambda: {
                "cpu_ns": 0,
                "wall_ns": 0,
                "preparations": 0,
                "cache_hits": 0,
                "failed_preparations": 0,
                "input_context_bytes": 0,
                "output_context_bytes": 0,
            }
        )
        self.last: dict[str, Any] | None = None

    def authority(self):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise ValueError("COMPRESSION_EXECUTION_WALL_LIMIT")
        if self.check:
            self.check()

    def prepare(self, model, text):
        self.authority()
        if model.key not in self.settings:
            raise ValueError("COMPRESSION_MODEL_VARIANT_NOT_APPROVED")
        cpu, wall = time.thread_time_ns(), time.perf_counter_ns()
        stats = self.stats[model.key]
        stats["preparations"] += 1
        self.last = None
        try:
            key = model.key, text
            if key in self.cache:
                stats["cache_hits"] += 1
                rendered, metadata = self.cache[key]
            else:
                parsed = ContextInput.parse(text)
                if len(parsed.query.encode()) > self.spec.max_query_bytes:
                    raise ValueError("COMPRESSION_QUERY_EXCEEDS_APPROVED_BYTE_BOUND")
                value = compress_context(parsed, self.settings[model.key], check=self.authority)
                rendered = value.render()
                metadata = {
                    "input_context_bytes": value.input_context_bytes,
                    "output_context_bytes": value.output_context_bytes,
                    "input_lines": value.input_lines,
                    "retained_lines": value.retained_lines,
                }
                needed = len(text.encode()) + len(rendered.encode())
                if self.cache_bytes + needed > 32 * 1048576:
                    raise ValueError("COMPRESSION_RAM_WORK_LIMIT")
                self.cache[key] = rendered, metadata
                self.cache_bytes += needed
                stats["input_context_bytes"] += value.input_context_bytes
                stats["output_context_bytes"] += value.output_context_bytes
            self.authority()
            # This field is supplied only to the existing opt-in encrypted case
            # recorder. Reports and metadata counters never include it.
            self.last = {"model_key": model.key, **metadata, "rendered_input": rendered}
            return rendered
        except Exception:
            stats["failed_preparations"] += 1
            raise
        finally:
            stats["cpu_ns"] += time.thread_time_ns() - cpu
            stats["wall_ns"] += time.perf_counter_ns() - wall

    def finalize(self, report):
        with localcontext(Context(prec=80)):
            return self._finalize(report)

    def _finalize(self, report):
        totals = {}
        for key, stats in self.stats.items():
            rate = self.spec.assumed_usd_per_cpu_hour
            quantum = self.spec.cpu_charge_quantum_ms * 1000000
            charged = max(quantum, ((stats["cpu_ns"] + quantum - 1) // quantum) * quantum)
            cpu_cost = (
                (Decimal(charged) * rate / Decimal(3600000000000)).quantize(
                    Decimal("0.000000000001"), rounding=ROUND_CEILING
                )
                if rate is not None
                else None
            )
            model = next((r for r in report["models"] if r["model"]["key"] == key), None)
            combined = (
                Decimal(model["known_cost_subtotal_usd"]) + cpu_cost
                if model is not None and model["cost_complete"] and cpu_cost is not None
                else None
            )
            totals[key] = {
                **stats,
                "assumed_cpu_cost_usd": str(cpu_cost) if cpu_cost is not None else None,
                "modeled_component_cost_usd": str(combined) if combined is not None else None,
            }
        base_key = (report.get("models") or [{}])[0].get("model", {}).get("key")
        base_raw = totals.get(base_key, {}).get("modeled_component_cost_usd")
        base = Decimal(base_raw) if isinstance(base_raw, str) else None
        for decision in report.get("recommendations", []):
            decision["compression_quality_assessment_passed"] = decision["quality_gate_passed"]
            raw = totals.get(decision["candidate"]["key"], {}).get("modeled_component_cost_usd")
            candidate = Decimal(raw) if isinstance(raw, str) else None
            known = base is not None and candidate is not None
            decision["sample_inference_cost_difference_usd"] = decision[
                "sample_modeled_cost_difference_usd"
            ]
            decision["sample_modeled_cost_difference_usd"] = (
                str(base - candidate) if base is not None and candidate is not None else None
            )
            decision["cheaper_on_sample"] = (
                candidate < base if candidate is not None and base is not None else None
            )
            decision["compression_automation_eligible"] = bool(
                candidate is not None
                and base is not None
                and decision["quality_gate_passed"]
                and decision["evidence_tier"] == 4
                and candidate < base
            )
            decision["economics_scope"] = (
                "native_api_plus_declared_compression_cpu_not_full_task_or_invoice"
            )
            if not known:
                decision.update(
                    status="NEEDS_VALIDATION",
                    quality_gate_passed=False,
                    decision_reason="compression_cpu_cost_assumption_or_native_usage_unknown",
                    next_action="declare_cpu_cost_basis_and_review_incomplete_usage",
                )
        report["compression"] = {
            **self.spec.public_summary(),
            "models": totals,
            "lineage_preparation_cpu_included": True,
            "byte_reduction_is_not_token_or_quality_equivalence": True,
            "full_task_cost_known": False,
        }
        report["recommendations"].sort(
            key=lambda r: Decimal(r.get("sample_modeled_cost_difference_usd") or "-Infinity"),
            reverse=True,
        )
        if isinstance(self.spec, JointModelContextExecutionSpec):
            baseline = (report.get("models") or [{}])[0].get("model", {})
            first = self.spec.variants[0].settings
            variants = {value.model_key: value.settings for value in self.spec.variants}
            for decision in report.get("recommendations", []):
                key = decision["candidate"]["key"]
                model = next(
                    value["model"] for value in report["models"] if value["model"]["key"] == key
                )
                decision["joint_model_context_confirmation_eligible"] = decision[
                    "compression_automation_eligible"
                ]
                decision["compression_automation_eligible"] = False
                decision["search_axes_changed"] = [
                    axis
                    for axis, changed in (
                        ("model", model["model"] != baseline["model"]),
                        ("output_cap", model["max_output_tokens"] != baseline["max_output_tokens"]),
                        ("extractive_context", variants[key] != first),
                    )
                    if changed
                ]
            report["joint_model_context"] = {
                "version": self.spec.version,
                "candidate_count": len(self.spec.variants) - 1,
                "split": self.split,
                "independent_confirmation_requires_lineage_receipt": True,
                "cost_evidence": "scripted_fixture_usage"
                if self.spec.transport == "scripted_test_only"
                else "executor_usage_and_declared_cpu_not_invoice",
                "full_task_cost_known": False,
            }
        return report
