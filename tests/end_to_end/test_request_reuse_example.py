"""Run the independent synthetic example as a separate actual SDK consumer process."""

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from support_http import owned_collector

from cutback.evals.artifacts import EncryptedFileStore
from cutback.server.db import ExperimentPlanRecord, ExperimentRunRecord, Project
from cutback.server.native_profile_db import NativeProfileAnalysis
from cutback.server.native_profile_service import run_native_profile_one


@pytest.mark.postgres
@pytest.mark.parametrize(
    "source_workload,prepare_trial", [(False, False), (True, False), (True, True)]
)
def test_independent_request_reuse_example_actual_sdk_http_worker_and_cleanup(
    api,
    tmp_path,
    monkeypatch,
    source_workload,
    prepare_trial,
):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    api[2].state.artifact_store = store
    slug = "owned-request-example-" + uuid4().hex
    stop = threading.Event()

    def worker():
        while not stop.wait(0.05):
            with api[2].state.sessions() as session:
                identifier = session.scalar(
                    select(NativeProfileAnalysis.id)
                    .join(Project)
                    .where(
                        Project.slug == slug,
                        NativeProfileAnalysis.state == "QUEUED",
                    )
                    .limit(1)
                )
            if identifier:
                run_native_profile_one(api[2].state.sessions, store, only_analysis_id=identifier)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    root = Path(__file__).resolve().parents[2]
    token = api[1]["Authorization"][7:]
    try:
        with owned_collector(api[2]) as endpoint:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(root / "examples/request-reuse/demo.py"),
                    "--endpoint",
                    endpoint,
                    "--project-slug",
                    slug,
                    *(["--source-workload"] if source_workload else []),
                    *(
                        ["--prepare-trial", "--keep-source", str(tmp_path / "retained-source")]
                        if prepare_trial
                        else []
                    ),
                ],
                cwd=tmp_path,
                env={**os.environ, "CUTBACK_CONTROL_TOKEN": token},
                capture_output=True,
                text=True,
                timeout=150 if prepare_trial else 90,
            )
        output = (completed.stdout + completed.stderr).replace(token, "<credential-omitted>")
        assert completed.returncode == 0, output
        receipt = json.loads(completed.stdout)
        assert receipt["synthetic_native_calls"] == 8 and receipt["paid_provider_calls"] == 0
        assert receipt["analysis"]["summary"]["modeled_potential_hits"] == 3
        assert receipt["project_retained"] is prepare_trial
        if source_workload:
            assessment = receipt["source_workload"]
            assert assessment["decision"] == "prepare_separate_cache_trial"
            assert assessment["selected_assessment"]["modeled_potential_hits"] == 3
            assert assessment["provider_calls"] == assessment["encrypted_storage_reads"] == 0
            assert assessment["paid_calls_authorized"] is False
            assert receipt["application_source_changed"] is False
        assert "request_hmac_sha256" not in completed.stdout
        if prepare_trial:
            from cutback.agent import load_session, workspace
            from cutback.agent_control import connect

            source = tmp_path / "retained-source"
            state = load_session(source)
            _, location = workspace(source)
            connection = json.loads((location / "connection.json").read_text())
            assert set(connection) == {"endpoint", "project"}
            assert connection["project"] == receipt["project_id"]
            with monkeypatch.context() as credentials:
                credentials.setenv("CUTBACK_CONTROL_TOKEN", token)
                resumed, project = connect(source)
                assert resumed.endpoint == endpoint and resumed.token == token
                assert project == receipt["project_id"]
            trial = receipt["pending_workload_trial"]
            assert state["phase"] == trial["phase"] == "PLAN_READY"
            assert trial["provider_calls"] == 0 and trial["approval_required"] is True
            assert receipt["source_checkout_retained"] is True
            assert (source / "app.py").read_bytes() == (
                root / "examples/source-workload/app.py"
            ).read_bytes()
            assert not any(
                token.encode() in path.read_bytes() for path in source.rglob("*") if path.is_file()
            )
            with api[2].state.sessions() as session:
                identifier = UUID(receipt["project_id"])
                assert (
                    session.query(ExperimentRunRecord).filter_by(project_id=identifier).count() == 0
                )
                plans = session.query(ExperimentPlanRecord).filter_by(project_id=identifier).all()
                assert len(plans) == 1 and plans[0].approval_status == "pending"
                assert (
                    plans[0].specification["public_specification"]["workload_portfolio"][
                        "assessment"
                    ]["decision"]
                    == "prepare_separate_cache_trial"
                )
            assert (
                api[0]
                .delete(
                    f"/v1/projects/{receipt['project_id']}",
                    headers=api[1],
                    params={"confirm_slug": slug},
                )
                .status_code
                == 200
            )
        with api[2].state.sessions() as session:
            assert session.scalar(select(Project.id).where(Project.slug == slug)) is None
        assert not any(path.is_file() for path in store.root.rglob("*"))
    finally:
        stop.set()
        thread.join(5)
    assert not thread.is_alive()
