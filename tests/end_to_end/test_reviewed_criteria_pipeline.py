"""Actual native SDK, encrypted store, HTTP and PostgreSQL; zero paid transport."""

import os
import subprocess
import threading
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from support_browser import browser_command
from test_pipeline import server

from cutback.demo_criteria import seed_criteria, synthetic_executor
from cutback.evals.artifacts import EncryptedFileStore
from cutback.server.criteria_worker import run_criteria_one
from cutback.server.db import ExperimentRunRecord, JobRecord
from cutback.server.worker import run_one

pytestmark = [pytest.mark.postgres, pytest.mark.e2e]


@pytest.mark.parametrize("semantic", [False, True])
def test_criteria_authenticated_generation_review_confirmation_and_accessibility(
    api, tenant, tmp_path, monkeypatch, semantic
):
    if os.getenv("CUTBACK_TEST_BROWSER") != "true":
        pytest.skip("set CUTBACK_TEST_BROWSER=true for authenticated criteria acceptance")
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    stop = threading.Event()
    if semantic:
        from cutback.demo_rubrics import seed_rubrics
        from cutback.demo_rubrics import synthetic_executor as rubric_executor

        executor, calls = rubric_executor()
    else:
        executor, calls = synthetic_executor()
    errors = []

    def worker():
        try:
            while not stop.wait(0.1):
                with api[2].state.sessions() as session:
                    found = session.execute(
                        select(ExperimentRunRecord.id, JobRecord.execution_kind)
                        .join(JobRecord, JobRecord.run_id == ExperimentRunRecord.id)
                        .where(
                            ExperimentRunRecord.project_id == UUID(tenant[0]),
                            ExperimentRunRecord.status == "QUEUED",
                        )
                        .order_by(ExperimentRunRecord.created_at)
                        .limit(1)
                    ).first()
                if found:
                    action = run_criteria_one if found[1] == "criteria_generation" else run_one
                    action(
                        api[2].state.sessions,
                        api[2].state.artifact_store,
                        executor=executor,
                        execution_enabled=True,
                        only_run_id=found[0],
                    )
        except Exception as exc:
            errors.append(type(exc).__name__)

    thread = threading.Thread(target=worker, daemon=True)
    token, viewer = api[1]["Authorization"][7:], tenant[2]["Authorization"][7:]
    try:
        with (
            server(api[2]) as endpoint,
            httpx.Client(base_url=endpoint, headers=api[1], trust_env=False) as control,
        ):
            state = (
                seed_rubrics(api[2], control, tenant[0], execute=False)
                if semantic
                else seed_criteria(api[2], control, tenant[0], execute=False)
            )
            thread.start()
            result = subprocess.run(
                browser_command(Path(__file__).resolve().parents[2] / "web", "criteria.spec.ts"),
                cwd=Path(__file__).resolve().parents[2] / "web",
                env={
                    **os.environ,
                    "CUTBACK_UI_URL": endpoint,
                    "CUTBACK_UI_KEY": token,
                    "CUTBACK_UI_PROJECT": tenant[0],
                    "CUTBACK_CRITERIA_BROWSER": "true",
                    "CUTBACK_CRITERIA_SEMANTIC": "true" if semantic else "false",
                    "CUTBACK_CRITERIA_ID": state["criteria_id"],
                    "CUTBACK_CRITERIA_DATASET": state["dataset_id"],
                    "CUTBACK_CRITERIA_VIEWER": viewer,
                    "CUTBACK_UI_OUTPUT_DIR": str(tmp_path / "criteria-browser-output"),
                },
                capture_output=True,
                text=True,
                timeout=180,
            )
        output = (
            (result.stdout + result.stderr)
            .replace(token, "<credential-omitted>")
            .replace(viewer, "<credential-omitted>")
        )
        assert result.returncode == 0, output
        assert not errors and len(calls) > 1
    finally:
        stop.set()
        if thread.ident:
            thread.join(timeout=10)
        executor.close()


def test_independent_criteria_sample_runs_review_validation_and_untouched_confirmation(
    api, tenant, tmp_path, monkeypatch
):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    with (
        server(api[2]) as endpoint,
        httpx.Client(base_url=endpoint, headers=api[1], trust_env=False) as control,
    ):
        result = seed_criteria(api[2], control, tenant[0], workspace=tmp_path / "source")
    assert result["native_sdk_calls"] > 1 and result["paid_provider_calls"] == 0
    assert result["production_changed"] is False
    assert result["criteria_generation_cost"]["billing_complete"] is True
    assert result["criteria_generation_cost"]["amortized_per_task_usd"] is None
    assert "gpt-4.1-mini" in result["source_change"]["diff"]
    assert "gpt-4.1" in result["source_change"]["rollback_diff"]
    assert result["source_change"]["production_deployed"] is False
    from support_retrieval_application import git

    from cutback.application_checks import ApplicationCommand, prepare, run_checks, verify

    original = Path(result["source_directory"])
    proposed = tmp_path / "proposed"
    git(tmp_path, "clone", "--no-hardlinks", str(original), str(proposed))
    patch = tmp_path / "source-change.patch"
    patch.write_text(result["source_change"]["diff"], encoding="utf-8", newline="")
    git(proposed, "apply", str(patch))
    git(proposed, "add", ".")
    git(
        proposed,
        "-c",
        "user.name=Cutback synthetic sample",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "Owned reviewed criteria candidate",
    )
    contract = prepare(
        original,
        proposed,
        [ApplicationCommand(key="native-classifier", arguments=["check_application.py"])],
    )
    receipts = run_checks(contract, original, proposed, execute_local=True)
    assert (
        verify(contract, receipts, original, proposed)["status"] == "PASSED_RECORDED_LOCAL_CHECKS"
    )
    patch.write_text(result["source_change"]["rollback_diff"], encoding="utf-8", newline="")
    git(proposed, "apply", str(patch))
    assert (proposed / "app.py").read_bytes() == (original / "app.py").read_bytes()
    assert (
        result["generation_run_id"] != result["validation_run_id"] != result["confirmation_run_id"]
    )
