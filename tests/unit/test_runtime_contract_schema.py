"""Runtime Unicode contracts must not leak interpreter-specific schema defaults."""

import pytest

from cutback.evals.compression import CompressionExecutionSpec
from cutback.evals.retrieval import RetrievalExecutionSpec


@pytest.mark.parametrize("model", [CompressionExecutionSpec, RetrievalExecutionSpec])
def test_contract_schema_stays_portable_but_runtime_hash_remains_required(model):
    field = model.model_fields["contract_sha256"]
    current = field.default_factory()
    assert len(current) == 64
    assert "default" not in model.model_json_schema()["properties"]["contract_sha256"]
    # Validation still compares the exact current runtime contract; never accept a foreign hash.
    with pytest.raises(ValueError, match="CONTRACT_CHANGED_REAPPROVAL_REQUIRED"):
        instance = model.model_construct(contract_sha256="0" * 64)
        model.valid(instance)
