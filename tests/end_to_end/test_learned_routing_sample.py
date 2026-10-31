"""Independent source demo through real HTTP, native SDK, encryption and PostgreSQL."""

import json
import os
import subprocess
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from sqlalchemy import event, select
from support_browser import browser_command
from test_pipeline import server

from cutback.demo_routing import seed_routing
from cutback.evals.artifacts import EncryptedFileStore
from cutback.server.db import ExperimentPlanRecord, ExperimentRunRecord, PayloadSample, Project
from cutback.server.execution_db import ExecutionCall

pytestmark = [pytest.mark.postgres, pytest.mark.e2e]


def test_native_routing_sample_has_distinct_stages_known_setup_and_private_cleanup(
    api, tenant, tmp_path, monkeypatch
):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    app = api[2]
    store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    app.state.artifact_store = store
    project = UUID(tenant[0])
    with (
        server(app) as endpoint,
        httpx.Client(base_url=endpoint, headers=api[1], trust_env=False, timeout=30) as control,
    ):
        sample = seed_routing(app, control, str(project), demo=True)
        assert sample["paid_provider_calls"] == 0 and sample["production_changed"] is False
        assert sample["actual_declared_checks_passed"] is True
        assert sample["source_patch_eligible"] is False and sample["full_task_cost_known"] is False
        runs = [
            UUID(sample[key])
            for key in ["training_run_id", "validation_run_id", "confirmation_run_id"]
        ]
        assert len(set(runs)) == 3
        with app.state.sessions() as session:
            records = [session.get(ExperimentRunRecord, identity) for identity in runs]
            assert all(
                row.status == "COMPLETED" and row.results["billing_complete"] is True
                for row in records
            )
            training, validation, confirmation = records
            assert training.results["recommendations"] == []
            assert confirmation.results["lineage"]["fresh_test_claim"] is True
            assert sample["native_sdk_calls"] == sum(row.requests_used for row in records)
            setup = confirmation.results["routing_setup_cost"]
            assert setup == validation.results["routing_setup_cost"]
            assert setup["shared_setup_key"] == str(training.id)
            assert Decimal(setup["training_known_cost_usd"]) == training.spent_usd
            assert setup["fit_compute_usd"] is None and setup["amortized_per_task_usd"] is None
            calls = session.scalars(
                select(ExecutionCall).where(ExecutionCall.run_id.in_(runs))
            ).all()
            assert len(calls) == sample["native_sdk_calls"] and all(
                row.spent_usd is not None for row in calls
            )
            fit = session.get(PayloadSample, UUID(sample["fit_id"]))
            fit_ref = fit.object_ref
            slug = session.get(Project, project).slug
        base = f"/v1/projects/{project}"
        exported = control.get(base + "/export")
        assert exported.status_code == 200
        assert (
            b"Priority: urgent." not in exported.content and b"object_ref" not in exported.content
        )
        assert control.delete(base, params={"confirm_slug": slug}).status_code == 200
        with pytest.raises((ValueError, FileNotFoundError)):
            store.get(project, fit_ref)


def test_authenticated_routing_authoring_mobile_accessibility_and_viewer_boundary(
    api, tenant, tmp_path, monkeypatch
):
    if os.getenv("CUTBACK_TEST_BROWSER") != "true":
        pytest.skip("set CUTBACK_TEST_BROWSER=true for authenticated routing acceptance")
    from cutback.demo_routing import routing_worker

    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    app = api[2]
    app.state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    storage_errors = []

    def storage_error(context):
        if len(storage_errors) < 32:
            error = context.original_exception
            storage_errors.append(
                {"type": type(error).__name__, "sqlstate": getattr(error, "sqlstate", None)}
            )

    event.listen(app.state.engine, "handle_error", storage_error)
    with (
        server(app) as endpoint,
        httpx.Client(base_url=endpoint, headers=api[1], trust_env=False, timeout=30) as control,
    ):
        seed_routing(app, control, tenant[0], execute=False, demo=True)
        token = api[1]["Authorization"][7:]
        viewer = tenant[2]["Authorization"][7:]
        web = Path(__file__).resolve().parents[2] / "web"
        with routing_worker(app, tenant[0]) as worker:
            result = subprocess.run(
                browser_command(web, "routing.spec.ts"),
                cwd=web,
                env={
                    **os.environ,
                    "CUTBACK_UI_URL": endpoint,
                    "CUTBACK_UI_KEY": token,
                    "CUTBACK_UI_PROJECT": tenant[0],
                    "CUTBACK_UI_OUTPUT_DIR": str(tmp_path / "routing-browser-output"),
                    "CUTBACK_ROUTING_BROWSER": "true",
                    "CUTBACK_ROUTING_VIEWER": viewer,
                },
                capture_output=True,
                text=True,
                timeout=300,
            )
            safe = (
                (result.stdout + result.stderr)
                .replace(token, "<credential-omitted>")
                .replace(viewer, "<credential-omitted>")
            )
            (tmp_path / "routing-storage-diagnostics.json").write_text(
                json.dumps(storage_errors), encoding="utf-8"
            )
            assert result.returncode == 0, (safe, storage_errors)
        assert worker["calls"] and not worker["errors"]
        with app.state.sessions() as session:
            plans = session.scalars(
                select(ExperimentPlanRecord).where(
                    ExperimentPlanRecord.project_id == UUID(tenant[0])
                )
            ).all()
            runs = session.scalars(
                select(ExperimentRunRecord).where(ExperimentRunRecord.project_id == UUID(tenant[0]))
            ).all()
            assert len(plans) == len(runs) == 3
            assert all(
                row.status == "COMPLETED" and row.results["billing_complete"] is True
                for row in runs
            )
            assert sum(row.requests_used for row in runs) == len(worker["calls"])
