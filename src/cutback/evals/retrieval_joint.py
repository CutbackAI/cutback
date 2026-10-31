"""Separate frozen contract for native model/output and corpus retrieval comparisons.

The original v6 fields and serialization remain independently defined. This
contract composes existing bounded lexical retrieval with qualified native text;
it does not authorize caches, tools, remote retrieval or arbitrary source code.
"""

from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from cutback.core.models import StrictModel
from cutback.core.retrieval import (
    CONTRACT_SHA256,
    RetrievalConfigurationBinding,
    RetrievalCorpus,
    RetrievalVariant,
)
from cutback.core.retrieval_producer import RetrievalProducerBinding
from cutback.evals.retrieval import AssumedCPURate, RetrievalExecutionSpec

JOINT_RETRIEVAL_CONTRACT: Literal["cutback.joint-model-retrieval.v1"] = (
    "cutback.joint-model-retrieval.v1"
)


class JointModelRetrievalExecutionSpec(StrictModel):
    version: Literal["cutback.joint-model-retrieval.v1"] = JOINT_RETRIEVAL_CONTRACT
    contract_sha256: str = Field(default_factory=lambda: CONTRACT_SHA256, pattern=r"^[a-f0-9]{64}$")
    corpus: RetrievalCorpus
    variants: tuple[RetrievalVariant, ...] = Field(min_length=2, max_length=6)
    max_query_bytes: int = Field(default=1024, strict=True, ge=1, le=8192)
    assumed_usd_per_cpu_hour: AssumedCPURate | None = None
    cpu_charge_quantum_ms: int = Field(default=1, strict=True, ge=1, le=1000)
    configuration_binding: RetrievalConfigurationBinding | None = None
    producer_binding: RetrievalProducerBinding | None = None

    def legacy(self):
        raw = self.model_dump(mode="json", exclude={"producer_binding"})
        raw["version"] = "cutback.retrieval-execution.v1"
        return RetrievalExecutionSpec.model_validate(raw)

    @model_validator(mode="after")
    def valid(self):
        self.legacy()
        producer, binding = self.producer_binding, self.configuration_binding
        if producer and (
            binding is None
            or producer.commit != binding.commit
            or producer.source_tree_hash != binding.source_tree_hash
            or binding.file != (PurePosixPath(producer.file).parent / "retrieval.json").as_posix()
        ):
            raise ValueError("JOINT_RETRIEVAL_PRODUCER_AND_SETTINGS_MUST_BIND_SAME_SOURCE")
        return self

    def input_byte_bound(self):
        return self.legacy().input_byte_bound()

    def public_summary(self):
        return {
            **self.legacy().public_summary(),
            "version": self.version,
            "producer_binding": self.producer_binding.model_dump(mode="json")
            if self.producer_binding
            else None,
            "search_axes": ["native_model", "output_ceiling", "retrieval_settings"],
        }
