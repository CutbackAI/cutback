"""Product sample uses actual scoped HTTP/PostgreSQL/native SDKs, with zero paid transport."""

import os
from pathlib import Path

import httpx
import pytest
from test_pipeline import server

from cutback.demo_rubrics import seed_rubrics
from cutback.evals.artifacts import EncryptedFileStore

pytestmark = [pytest.mark.postgres, pytest.mark.e2e]


def test_owned_rubric_sample_completes_training_review_validation_and_untouched_proxy_confirmation(
    api, tenant, tmp_path, monkeypatch
):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(Path(tmp_path) / "encrypted", os.urandom(32))
    with (
        server(api[2]) as endpoint,
        httpx.Client(base_url=endpoint, headers=api[1], trust_env=False) as control,
    ):
        result = seed_rubrics(api[2], control, tenant[0])
        assert result["paid_provider_calls"] == 0 and result["native_fixture_calls"] > 1
        validation = control.get(
            f"/v1/projects/{tenant[0]}/experiments/{result['validation_run_id']}"
        ).json()["results"]
        decisions = {row["candidate"]["key"]: row for row in validation["recommendations"]}
        assert decisions["candidate"]["quality_gate_passed"] is True
        assert decisions["unsafe"]["quality_gate_passed"] is False
        final = control.get(
            f"/v1/projects/{tenant[0]}/experiments/{result['confirmation_run_id']}"
        ).json()["results"]
        assert final["lineage"]["fresh_test_claim"] is True
        assert final["recommendations"][0]["status"] == "MODEL_PROXY_COMPARISON_ONLY"
        assert (
            result["semantic_quality"] == "NOT VERIFIED"
            and result["source_patch_eligible"] is False
        )
