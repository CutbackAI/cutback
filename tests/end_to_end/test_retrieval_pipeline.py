"""Installed-capable SDK/HTTP/PostgreSQL/source-check/browser acceptance. No paid transport."""

import json
import os
import subprocess
import threading
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from support_browser import browser_command
from support_retrieval_application import git
from test_pipeline import server

from cutback.demo_retrieval import seed_retrieval, synthetic_executor
from cutback.server.db import ExperimentPlanRecord, ExperimentRunRecord
from cutback.server.execution_db import ExecutionCall
from cutback.server.worker import run_one

pytestmark = [pytest.mark.postgres, pytest.mark.e2e]


def setup(api, tenant, tmp_path, monkeypatch):
    from cutback.evals.artifacts import EncryptedFileStore

    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    return f"/v1/projects/{tenant[0]}"


def test_corpus_native_pipeline_prepares_exact_diff_and_passes_both_application_trees(
    api, tenant, tmp_path, monkeypatch
):
    from cutback.application_check_discovery import discover
    from cutback.application_checks import ApplicationCheckContract, run_checks, verify
    from cutback.retrieval_changes import review_change

    setup(api, tenant, tmp_path, monkeypatch)
    with (
        server(api[2]) as endpoint,
        httpx.Client(base_url=endpoint, headers=api[1], trust_env=False) as control,
    ):
        result = seed_retrieval(api[2], control, tenant[0], tmp_path / "workspace")
    assert result["paid_provider_calls"] == 0
    artifact = result["source_change"]
    assert artifact["proposal"]["decision"] == "synthetic_local_review_only"
    root = Path(result["source_directory"])
    proposed = tmp_path / "proposed"
    git(tmp_path, "clone", "--no-hardlinks", str(root), str(proposed))
    patch = tmp_path / "change.patch"
    patch.write_text(artifact["diff"], encoding="utf-8", newline="")
    git(proposed, "apply", str(patch))
    git(proposed, "add", ".")
    git(
        proposed,
        "-c",
        "user.name=Synthetic Test",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "Review retrieval configuration",
    )
    contract = ApplicationCheckContract.model_validate(discover(root, proposed)["contract"])
    receipt = run_checks(contract, root, proposed, execute_local=True)
    assert receipt["all_passed"]
    assert verify(contract, receipt, root, proposed)["status"] == "PASSED_RECORDED_LOCAL_CHECKS"
    coverage = {"support-rag": [command.key for command in contract.commands]}
    review = review_change(
        artifact,
        contract.model_dump(mode="json"),
        receipt,
        root,
        proposed,
        coverage,
        branch="codex/retrieval-review",
    )
    assert review["status"] == "READY_FOR_LOCAL_REVIEW" and not review["production_deployed"]
    assert review["local_review_ref"] == "refs/heads/codex/retrieval-review"
    assert (
        review_change(
            artifact,
            contract.model_dump(mode="json"),
            receipt,
            root,
            proposed,
            coverage,
            branch="codex/retrieval-review",
        )
        == review
    )
    with pytest.raises(ValueError, match="DECLARE_SETTINGS"):
        review_change(artifact, contract.model_dump(mode="json"), receipt, root, proposed, {})
    git(proposed, "branch", "different-ref", contract.base.commit)
    with pytest.raises(ValueError, match="POINTS_ELSEWHERE"):
        review_change(
            artifact,
            contract.model_dump(mode="json"),
            receipt,
            root,
            proposed,
            coverage,
            branch="different-ref",
        )
    rollback = tmp_path / "rollback.patch"
    rollback.write_text(artifact["rollback_diff"], encoding="utf-8", newline="")
    git(proposed, "apply", "--check", str(rollback))
    with api[2].state.sessions() as session:
        runs = list(
            session.scalars(
                select(ExperimentRunRecord)
                .join(ExperimentPlanRecord)
                .where(
                    ExperimentPlanRecord.project_id == UUID(tenant[0]),
                    ExperimentPlanRecord.specification["public_specification"][
                        "workflow"
                    ].as_string()
                    == "synthetic-retrieval-search",
                )
            )
        )
        calls = list(
            session.scalars(
                select(ExecutionCall).where(ExecutionCall.run_id.in_([r.id for r in runs]))
            )
        )
        assert len(runs) == 2 and len(calls) == result["native_sdk_calls"]
        assert all(
            r.results["billing_complete"] and r.requests_used == r.known_cost_requests for r in runs
        )
        assert all("deadline-days=" not in json.dumps(r.results) for r in runs)


def test_authenticated_retrieval_authoring_confirmation_and_mobile_accessibility(
    api, tenant, tmp_path, monkeypatch
):
    if os.getenv("CUTBACK_TEST_BROWSER") != "true":
        pytest.skip("set CUTBACK_TEST_BROWSER=true for authenticated retrieval/browser acceptance")
    setup(api, tenant, tmp_path, monkeypatch)
    stop = threading.Event()
    errors = []
    executor, calls = synthetic_executor()

    def worker():
        try:
            while not stop.wait(0.1):
                with api[2].state.sessions() as session:
                    run = session.scalar(
                        select(ExperimentRunRecord)
                        .where(
                            ExperimentRunRecord.project_id == UUID(tenant[0]),
                            ExperimentRunRecord.status == "QUEUED",
                        )
                        .order_by(ExperimentRunRecord.created_at)
                    )
                    run_id = run.id if run else None
                if run_id:
                    run_one(
                        api[2].state.sessions,
                        api[2].state.artifact_store,
                        executor=executor,
                        execution_enabled=True,
                        only_run_id=run_id,
                    )
        except Exception as exc:
            errors.append(type(exc).__name__)

    thread = threading.Thread(target=worker, daemon=True)
    token = api[1]["Authorization"][7:]
    viewer = tenant[2]["Authorization"][7:]
    try:
        with (
            server(api[2]) as endpoint,
            httpx.Client(base_url=endpoint, headers=api[1], trust_env=False) as control,
        ):
            result = seed_retrieval(
                api[2], control, tenant[0], tmp_path / "browser-source", execute=False
            )
            root = Path(result["source_directory"])
            inspection = tmp_path / "inspection.json"
            inspection.write_text(
                json.dumps({"configuration_binding": result["configuration_binding"]}),
                encoding="utf-8",
            )
            thread.start()
            completed = subprocess.run(
                browser_command(Path(__file__).resolve().parents[2] / "web", "retrieval.spec.ts"),
                cwd=Path(__file__).resolve().parents[2] / "web",
                env={
                    **os.environ,
                    "CUTBACK_UI_URL": endpoint,
                    "CUTBACK_UI_KEY": token,
                    "CUTBACK_UI_PROJECT": tenant[0],
                    "CUTBACK_UI_OUTPUT_DIR": str(tmp_path / "retrieval-browser-output"),
                    "CUTBACK_RETRIEVAL_BROWSER": "true",
                    "CUTBACK_RETRIEVAL_VIEWER": viewer,
                    "CUTBACK_RETRIEVAL_CORPUS": str(root / "corpus.json"),
                    "CUTBACK_RETRIEVAL_INSPECTION": str(inspection),
                },
                capture_output=True,
                text=True,
                timeout=180,
            )
            output = (
                (completed.stdout + completed.stderr)
                .replace(token, "<credential-omitted>")
                .replace(viewer, "<credential-omitted>")
            )
            assert completed.returncode == 0, output
    finally:
        stop.set()
        if thread.ident:
            thread.join(timeout=10)
        executor.close()
    assert not errors and len(calls) > 0
