"""Independent three-category dataset through actual batch SDK and real PostgreSQL."""

import os

import pytest

from cutback.demo_provider_batches import seed_provider_batch
from cutback.evals.artifacts import EncryptedFileStore

pytestmark = pytest.mark.postgres


def test_owned_three_label_batch_sample_is_complete_descriptive_and_cleans_vendor_files(
    api, tenant, tmp_path, monkeypatch
):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    result = seed_provider_batch(api[2], api[0], tenant[0], headers=api[1])
    assert result["status"] == "COMPLETED"
    assert result["reference_labels"] == 3 and result["declared_intent_families"] == 60
    assert result["paid_provider_calls"] == 0 and result["provider_batch_submissions"] == 2
    assert result["vendor_files_deleted"] is True
    assert result["report"]["billing_complete"] is True
    assert result["report"]["heldout_quality_claim"] is False
    assert result["report"]["per_request_latency_ms"] is None
    for model in result["report"]["models"]:
        assert model["requests"] == model["known_cost_requests"] == 48
        assert model["runs"][0]["metric"]["value"] == 1
        assert model["runs"][0]["metric"]["independent_group_n"] == 12
        assert model["runs"][0]["criteria"][0]["metric"]["value"] == 1
