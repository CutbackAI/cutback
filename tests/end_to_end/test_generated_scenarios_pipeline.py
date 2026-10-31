"""Independent HTTP, native SDK, database, review and development evaluation sample."""

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from support_browser import browser_command
from test_pipeline import server

from cutback.demo import sample_cases
from cutback.demo_scenarios import seed_scenarios
from cutback.evals.artifacts import EncryptedFileStore
from cutback.server.db import DatasetItem, ExperimentRunRecord, PayloadSample, Project

pytestmark = [pytest.mark.postgres, pytest.mark.e2e]


def test_independent_scenarios_generate_review_import_and_validate(
    api, tenant, tmp_path, monkeypatch
):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    with (
        server(api[2]) as endpoint,
        httpx.Client(base_url=endpoint, headers=api[1], trust_env=False, timeout=30) as control,
    ):
        # The combined demo already has protected families from the larger ticket sample.
        # Scenario development cases must remain independent of that cohort.
        base = f"/v1/projects/{tenant[0]}"
        assert control.post(base + "/environments", json={"name": "staging"}).status_code == 201
        policy = control.post(
            base + "/capture-policies",
            json={
                "enabled": True,
                "approval_ref": "synthetic-original-demo-source",
                "rights_basis": "synthetic",
                "environments": ["staging"],
                "workflows": ["synthetic-existing-ticket-cohort"],
                "allowed_providers": ["openai"],
                "sample_rate": 1,
                "max_samples": 200,
                "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            },
        )
        assert policy.status_code == 201, policy.text
        existing = control.post(
            base + "/datasets",
            json={
                "workflow": "synthetic-existing-ticket-cohort",
                "policy_id": policy.json()["id"],
                "source_type": "synthetic_fixture",
                "provenance_ref": "synthetic-existing-ticket-source",
                "independent_labels": False,
                "cases": sample_cases()[:80],
                "split_seed": 29,
            },
        )
        assert existing.status_code == 201, existing.text
        with api[2].state.sessions() as session:
            protected_ordinals = session.scalars(
                select(PayloadSample.metadata_safe)
                .join(DatasetItem, DatasetItem.payload_id == PayloadSample.id)
                .where(
                    DatasetItem.dataset_id == UUID(existing.json()["id"]),
                    DatasetItem.split == "test",
                )
            ).all()
        protected = [sample_cases()[value["ordinal"]] for value in protected_ordinals]
        assert protected
        with monkeypatch.context() as scoped:
            scoped.setattr("cutback.demo_scenarios.scenario_cases", lambda: protected)
            with pytest.raises(ValueError, match="SYNTHETIC_SCENARIO_CONTROL_FAILED_422"):
                seed_scenarios(api[2], control, tenant[0], execute=False)
        receipt = seed_scenarios(api[2], control, tenant[0])
        assert receipt["paid_provider_calls"] == 0
        assert receipt["production_changed"] is False and receipt["test_evidence"] is False
        assert receipt["independent_labels"] is False and receipt["representative"] is False
        assert receipt["reactive_user_simulation"] is False
        assert receipt["idempotent_import_checked"] is True
        assert receipt["native_sdk_calls"] == 11
        assert receipt["coverage_scope"] == "proposer_declared_not_measured"
        with api[2].state.sessions() as session:
            generated = session.get(ExperimentRunRecord, UUID(receipt["generation_run_id"]))
            validated = session.get(ExperimentRunRecord, UUID(receipt["validation_run_id"]))
            assert generated.requests_used == generated.known_cost_requests == 1
            assert validated.status == "COMPLETED" and validated.requests_used == 10
            assert validated.results["lineage"]["fresh_test_claim"] is False
            slug = session.get(Project, UUID(tenant[0])).slug
        exported = control.get(f"/v1/projects/{tenant[0]}/export")
        assert exported.status_code == 200
        assert b"limited time" not in exported.content
        assert b"Please keep the response concise" not in exported.content
        assert b"object_ref" not in exported.content
        assert b"generated_scenario_versions" in exported.content
        assert (
            control.delete(f"/v1/projects/{tenant[0]}", params={"confirm_slug": slug}).status_code
            == 200
        )


def test_authenticated_scenario_authoring_review_import_mobile_and_accessibility(
    api, tenant, tmp_path, monkeypatch
):
    from cutback.demo_scenarios import scenario_worker

    if os.getenv("CUTBACK_TEST_BROWSER") != "true":
        pytest.skip("set CUTBACK_TEST_BROWSER=true for authenticated scenario acceptance")
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    token, viewer = api[1]["Authorization"][7:], tenant[2]["Authorization"][7:]
    with (
        scenario_worker(api[2], tenant[0]) as (calls, errors),
        server(api[2]) as endpoint,
        httpx.Client(base_url=endpoint, headers=api[1], trust_env=False) as control,
    ):
        state = seed_scenarios(api[2], control, tenant[0], execute=False)
        web = Path(__file__).resolve().parents[2] / "web"
        result = subprocess.run(
            browser_command(web, "scenarios.spec.ts"),
            cwd=web,
            env={
                **os.environ,
                "CUTBACK_UI_URL": endpoint,
                "CUTBACK_UI_KEY": token,
                "CUTBACK_UI_PROJECT": tenant[0],
                "CUTBACK_SCENARIO_BROWSER": "true",
                "CUTBACK_SCENARIO_DATASET": state["source_dataset_id"],
                "CUTBACK_SCENARIO_VIEWER": viewer,
                "CUTBACK_UI_OUTPUT_DIR": str(tmp_path / "scenario-browser-output"),
            },
            capture_output=True,
            text=True,
            timeout=180,
        )
        safe = (
            (result.stdout + result.stderr)
            .replace(token, "<credential-omitted>")
            .replace(viewer, "<credential-omitted>")
        )
        if result.returncode:
            pytest.fail(safe, pytrace=False)
        assert not errors and len(calls) == 1
