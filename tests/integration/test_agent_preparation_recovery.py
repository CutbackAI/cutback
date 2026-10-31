"""Lost setup replies resume from the private journal without queueing inference."""

import json
import os
from uuid import UUID

import pytest
from sqlalchemy import func, select

from cutback.agent import analyze, load_session, next_action, prepare
from cutback.agent_control import Control
from cutback.agent_preparation import resume
from cutback.evals.artifacts import EncryptedFileStore
from cutback.server.db import (
    CapturePolicyRecord,
    DatasetRecord,
    ExperimentRunRecord,
    PayloadSample,
    ScanRun,
)

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("stage", ["scans", "capture-policies", "datasets", "experiments/plans"])
def test_discarded_client_identity_blocks_repeated_uploads_until_reconciliation(
    api, tenant, tmp_path, monkeypatch, stage
):
    root, control, options = setup_application(api, tenant, tmp_path, monkeypatch, "native")

    class StripsIdentity:
        endpoint = control.endpoint
        writes = 0

        def __call__(self, method, path, body=None):
            route = path.split("?", 1)[0]
            if method == "POST":
                self.writes += 1
            if method == "POST" and route.endswith("/" + stage):
                if stage == "experiments/plans":
                    body = {key: value for key, value in body.items() if key != "request_id"}
                path = route
            return control(method, path, body)

    proxy = StripsIdentity()
    with pytest.raises(ValueError, match="IDENTITY_MISMATCH"):
        prepare(root, proxy, tenant[0], **options)
    before = proxy.writes
    with pytest.raises(ValueError, match="IDENTITY_RECONCILIATION_REQUIRED"):
        resume(root, proxy)
    assert proxy.writes == before
    pending = load_session(root)
    assert next_action(pending)["code"] == "RECONCILE_PREPARATION_IDENTITY_WITH_OPERATOR"
    assert pending["preparation_conflict"]["resource"] == stage
    assert pending["phase"] == "PREPARING"


def test_older_collector_is_refused_before_upload_then_same_setup_can_resume(
    api, tenant, tmp_path, monkeypatch
):
    root, control, options = setup_application(api, tenant, tmp_path, monkeypatch, "native")

    class OlderCollector:
        endpoint = control.endpoint
        writes = 0

        def __call__(self, method, path, body=None):
            if method == "GET" and path == "/v1/health/ready":
                return {"status": "ready", "schema_revision": "0030"}
            self.writes += 1
            raise AssertionError("No upload to a collector without durable preparation support")

    older = OlderCollector()
    with pytest.raises(ValueError, match="COLLECTOR_DURABLE_PREPARATION_UPGRADE_REQUIRED"):
        prepare(root, older, tenant[0], **options)
    assert older.writes == 0
    original = load_session(root)["preparation_intent"]
    completed = resume(root, control)
    assert completed["phase"] == "PLAN_READY" and completed["preparation_intent"] == original


@pytest.mark.parametrize("changed", ["source", "budget", "unknown_option"])
def test_resume_never_reinterprets_frozen_setup_authority(
    api, tenant, tmp_path, monkeypatch, changed
):
    from cutback.core.workspace import write_private

    root, control, options = setup_application(api, tenant, tmp_path, monkeypatch, "native")

    class FailAfterScan:
        endpoint = control.endpoint
        writes = 0

        def __call__(self, method, path, body=None):
            result = control(method, path, body)
            if method == "POST":
                self.writes += 1
                raise ConnectionError("synthetic committed setup reply lost")
            return result

    failed = FailAfterScan()
    with pytest.raises(ConnectionError):
        prepare(root, failed, tenant[0], **options)
    if changed == "source":
        path = root / "app.py"
        path.write_bytes(path.read_bytes() + b"\n# changed source\n")
        reason = "SOURCE_CHANGED"
    else:
        state = load_session(root)
        if changed == "budget":
            state["preparation_command"]["max_usd"] = "9"
            reason = "FROZEN_PLAN_CHANGED"
        else:
            state["preparation_command"]["unrecognized"] = "synthetic"
            reason = "PREPARATION_OPTIONS_INVALID"
        write_private(root / ".cutback/agent/session.json", state)
    before = failed.writes
    with pytest.raises(ValueError, match=reason):
        resume(root, failed)
    assert failed.writes == before


def setup_application(api, tenant, tmp_path, monkeypatch, kind):
    if kind == "native":
        from support_agent import fresh_application

        root = tmp_path / "application"
        root.mkdir()
        fresh_application(root)
        files = ["cases.jsonl"]
    elif kind == "context":
        from support_compression_application import application

        root = application(tmp_path)
        files = ["questions.jsonl"]
    else:
        from support_routing_application import application

        root = application(tmp_path)
        files = ["tickets.jsonl"]
    analyze(root)
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    control = Control("http://127.0.0.1", api[1]["Authorization"][7:], client=api[0])
    options = dict(
        files=files,
        rights_basis="synthetic",
        rights_ref="owned-recovery-test",
        max_usd="4",
        max_requests=500,
        minimum=0.9,
        independent_labels=True,
        min_test_samples=3,
        automatic=True,
    )
    if kind == "context":
        options["assumed_usd_per_cpu_hour"] = "0.01"
    if kind == "routing":
        options["fixture_transport"] = True
    return root, control, options


@pytest.mark.parametrize(
    "kind,stage",
    [
        ("native", "scans"),
        ("native", "capture-policies"),
        ("native", "datasets"),
        ("native", "experiments/plans"),
        ("context", "scans"),
        ("context", "capture-policies"),
        ("context", "datasets"),
        ("context", "experiments/plans"),
        ("routing", "capture-policies"),
        ("routing", "datasets"),
    ],
)
def test_lost_committed_setup_reply_resumes_identically_without_inference(
    api, tenant, tmp_path, monkeypatch, kind, stage
):
    root, control, options = setup_application(api, tenant, tmp_path, monkeypatch, kind)

    class LoseOnce:
        endpoint = control.endpoint
        lost = False
        writes = 0

        def __call__(self, method, path, body=None):
            route = path.split("?", 1)[0]
            if method == "POST":
                assert not route.endswith(("/approve", "/experiments"))
                self.writes += 1
            result = control(method, path, body)
            if method == "POST" and route.endswith("/" + stage) and not self.lost:
                self.lost = True
                raise ConnectionError("synthetic committed setup reply lost")
            return result

    interrupted = LoseOnce()
    with pytest.raises(ConnectionError, match="synthetic committed setup reply lost"):
        prepare(root, interrupted, tenant[0], **options)
    saved = load_session(root)
    assert saved["phase"] == "PREPARING"
    assert next_action(saved)["code"] == "RESUME_SAME_NONBILLABLE_PREPARATION"
    frozen = saved["preparation_intent"]
    assert "Owned synthetic classification task" not in json.dumps(saved)
    recovered = resume(root, interrupted)
    assert recovered["phase"] == "PLAN_READY" and recovered["plan"]["paid_calls_made"] == 0
    assert recovered["preparation_intent"] == frozen
    assert recovered["policy_id"] == frozen["requests"]["policy"]
    assert recovered["dataset_id"] == frozen["requests"]["dataset"]
    before = interrupted.writes
    assert resume(root, interrupted)["plan"] == recovered["plan"]
    assert interrupted.writes == before
    with api[2].state.sessions() as session:
        project = UUID(tenant[0])
        for table in (CapturePolicyRecord, DatasetRecord):
            assert (
                session.scalar(
                    select(func.count()).select_from(table).where(table.project_id == project)
                )
                == 1
            )
        assert session.scalar(
            select(func.count()).select_from(ScanRun).where(ScanRun.project_id == project)
        ) == (0 if kind == "routing" else 1)
        dataset = session.get(DatasetRecord, UUID(recovered["dataset_id"]))
        assert (
            session.scalar(
                select(func.count())
                .select_from(PayloadSample)
                .where(PayloadSample.project_id == project)
            )
            == dataset.provenance["case_count"]
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(ExperimentRunRecord)
                .where(ExperimentRunRecord.project_id == project)
            )
            == 0
        )
