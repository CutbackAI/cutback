"""Real PostgreSQL source workflows with a native SDK, never a live transport."""

from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select
from support_agent import native_response, prepare_fresh

from cutback.agent import advance, analyze, confirm, gate, load_session, prepare, proposal
from cutback.evals.executors import OpenAIExecutor
from cutback.server.db import CapturePolicyRecord, DatasetRecord, ExperimentRunRecord
from cutback.server.execution_db import ExecutionCall
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("minimum_cases", [6, 60])
@pytest.mark.parametrize("kind", ["classifier", "extractor"])
def test_native_preparation_freezes_the_requested_evidence_sample_minimum(
    api, tenant, tmp_path, monkeypatch, minimum_cases, kind
):
    root, control, state, options = prepare_fresh(
        api,
        tenant,
        tmp_path,
        monkeypatch,
        kind=kind,
        fixture_transport=kind == "classifier",
        min_test_samples=minimum_cases,
    )
    content = control(
        "POST",
        f"/v1/projects/{tenant[0]}/experiments/plans/{state['plan']['id']}/content",
        {"purpose": "owned-fixture-evidence-policy-check"},
    )
    assert content["specification"]["metric"]["min_test_samples"] == minimum_cases
    assert all(
        item["metric"]["min_test_samples"] == minimum_cases
        for item in content["specification"]["criteria"]
    )
    repeated = prepare(root, control, tenant[0], **options)
    assert repeated["plan"]["plan_hash"] == state["plan"]["plan_hash"]


def test_fixture_transport_intent_cannot_be_removed_after_preparation(
    api, tenant, tmp_path, monkeypatch
):
    root, control, state, options = prepare_fresh(
        api, tenant, tmp_path, monkeypatch, fixture_transport=True
    )
    writes = []

    class RecordingControl:
        endpoint = control.endpoint

        def __call__(self, method, path, body=None):
            if method == "POST":
                writes.append(path)
            return control(method, path, body)

    with pytest.raises(ValueError, match="FROZEN_PLAN_CHANGED"):
        prepare(root, RecordingControl(), tenant[0], **{**options, "fixture_transport": False})
    assert not writes
    assert load_session(root)["plan"]["plan_hash"] == state["plan"]["plan_hash"]


def test_old_collector_is_refused_before_upload_and_saved_fixture_setup_resumes(
    api, tenant, tmp_path, monkeypatch
):
    import os

    from support_agent import fresh_application

    from cutback.agent_control import Control
    from cutback.agent_preparation import resume
    from cutback.core.preparation import PREPARATION_CONTRACT
    from cutback.evals.artifacts import EncryptedFileStore

    root = tmp_path / "fixture-application"
    root.mkdir()
    fresh_application(root)
    analyze(root)
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    control = Control("http://127.0.0.1", api[1]["Authorization"][7:], client=api[0])
    writes = []

    class OlderCollector:
        endpoint = control.endpoint

        def __call__(self, method, path, body=None):
            if method == "GET" and path == "/v1/health/ready":
                return {"preparation_contract": PREPARATION_CONTRACT}
            writes.append(path)
            raise AssertionError("INCOMPATIBLE_COLLECTOR_MUST_NOT_RECEIVE_CONTENT")

    with pytest.raises(ValueError, match="COLLECTOR_NATIVE_FIXTURE_UPGRADE_REQUIRED"):
        prepare(
            root,
            OlderCollector(),
            tenant[0],
            files=["cases.jsonl"],
            rights_basis="synthetic",
            rights_ref="owned-fixture-protocol",
            max_usd="5",
            max_requests=1000,
            minimum=0.7,
            independent_labels=True,
            fixture_transport=True,
        )
    assert not writes
    frozen = load_session(root)["preparation_intent"]
    recovered = resume(root, control)
    assert recovered["phase"] == "PLAN_READY"
    assert recovered["preparation_intent"] == frozen
    assert recovered["plan"]["native_fixture"]["real_model_quality_verified"] is False


@pytest.mark.parametrize("changed", ["data", "rights"])
def test_fixture_plan_creation_rechecks_actual_synthetic_data_and_rights(
    api, tenant, tmp_path, monkeypatch, changed
):
    root, control, state, _ = prepare_fresh(
        api, tenant, tmp_path, monkeypatch, fixture_transport=True
    )
    content = control(
        "POST",
        f"/v1/projects/{tenant[0]}/experiments/plans/{state['plan']['id']}/content",
        {"purpose": "owned-fixture-scope-check"},
    )
    dataset_id = UUID(load_session(root)["dataset_id"])
    with api[2].state.sessions.begin() as session:
        dataset = session.get(DatasetRecord, dataset_id)
        if changed == "data":
            dataset.provenance = {**dataset.provenance, "source_type": "application_records"}
        else:
            policy = session.get(CapturePolicyRecord, UUID(dataset.provenance["policy_id"]))
            policy.policy = {**policy.policy, "rights_basis": "owner_authorized"}
    response = api[0].post(
        f"/v1/projects/{tenant[0]}/experiments/plans",
        headers=api[1],
        json={
            "request_id": str(uuid4()),
            "dataset_id": str(dataset_id),
            "specification": content["specification"],
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] == "NATIVE_FIXTURE_REQUIRES_SYNTHETIC_DATA_AND_RIGHTS"


def test_fixture_rights_change_after_first_native_call_stops_remaining_dispatch(
    api, tenant, tmp_path, monkeypatch
):
    root, control, state, _ = prepare_fresh(
        api, tenant, tmp_path, monkeypatch, fixture_transport=True
    )
    queued = advance(
        root,
        control,
        plan_hash=state["plan"]["plan_hash"],
        approval_ref="synthetic-revocation-check",
    )
    policy_id = UUID(load_session(root)["policy_id"])
    calls = []

    def handle(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            with api[2].state.sessions.begin() as session:
                policy = session.get(CapturePolicyRecord, policy_id)
                policy.policy = {**policy.policy, "rights_basis": "owner_authorized"}
        return native_response(request)

    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "owned-synthetic-only")
    native = OpenAIExecutor(
        http_client=httpx.Client(transport=httpx.MockTransport(handle), trust_env=False)
    )

    class FixtureExecutor:
        synthetic_transport = True

        def execute(self, model, text, timeout_seconds):
            return native.execute(model, text, timeout_seconds)

    try:
        result = run_one(
            api[2].state.sessions,
            api[2].state.artifact_store,
            executor=FixtureExecutor(),
            execution_enabled=True,
            only_run_id=queued["run_id"],
        )
        assert result["status"] == "FAILED"
        assert len(calls) == 1
        repeated = run_one(
            api[2].state.sessions,
            api[2].state.artifact_store,
            executor=FixtureExecutor(),
            execution_enabled=True,
            only_run_id=queued["run_id"],
        )
        assert repeated["status"] == "IDLE" and len(calls) == 1
        with api[2].state.sessions() as session:
            assert session.get(ExperimentRunRecord, UUID(queued["run_id"])).requests_used == 1
    finally:
        native.close()


def test_live_worker_refuses_fixture_plan_before_sdk_construction_or_reservation(
    api, tenant, tmp_path, monkeypatch
):
    root, control, state, _ = prepare_fresh(
        api, tenant, tmp_path, monkeypatch, fixture_transport=True
    )
    assert state["plan"]["native_fixture"]["real_model_quality_verified"] is False
    queued = advance(
        root, control, plan_hash=state["plan"]["plan_hash"], approval_ref="synthetic-only-command"
    )
    constructed = []

    def forbidden(**kwargs):
        constructed.append(True)
        raise AssertionError("LIVE_SDK_MUST_NOT_BE_CONSTRUCTED")

    with monkeypatch.context() as scope:
        scope.setattr("cutback.evals.executors.OpenAIExecutor", forbidden)
        blocked = run_one(
            api[2].state.sessions,
            api[2].state.artifact_store,
            execution_enabled=True,
            only_run_id=queued["run_id"],
        )
    assert blocked["status"] == "BLOCKED" and blocked["provider_calls"] == 0
    assert not constructed
    with api[2].state.sessions() as session:
        row = session.get(ExperimentRunRecord, UUID(queued["run_id"]))
        assert row.requests_used == 0 and row.reserved_usd == 0
        assert row.results["code"] == "SCRIPTED_NATIVE_REQUIRES_EXPLICIT_SYNTHETIC_EXECUTOR"
        assert (
            session.scalar(
                select(func.count())
                .select_from(ExecutionCall)
                .where(ExecutionCall.run_id == row.id)
            )
            == 0
        )


def test_native_fixture_search_confirmation_patch_and_real_quality_refusal(
    api, tenant, tmp_path, monkeypatch
):
    root, control, state, _ = prepare_fresh(
        api, tenant, tmp_path, monkeypatch, search=True, fixture_transport=True
    )
    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "owned-synthetic-only")
    calls = []

    def handle(request):
        calls.append(request.url.path)
        return native_response(request)

    native = OpenAIExecutor(
        http_client=httpx.Client(transport=httpx.MockTransport(handle), trust_env=False)
    )

    class FixtureExecutor:
        synthetic_transport = True

        def execute(self, model, text, timeout_seconds):
            assert model.provider == "openai", "UNSUPPORTED_FIXTURE_PROVIDER"
            return native.execute(model, text, timeout_seconds)

    try:
        queued = advance(
            root, control, plan_hash=state["plan"]["plan_hash"], approval_ref="synthetic-validation"
        )
        result = run_one(
            api[2].state.sessions,
            api[2].state.artifact_store,
            executor=FixtureExecutor(),
            execution_enabled=True,
            only_run_id=queued["run_id"],
        )
        assert result["status"] == "COMPLETED"
        completed = advance(root, control)
        report = control("GET", f"/v1/projects/{tenant[0]}/experiments/{completed['run_id']}")
        winner = next(
            row["candidate"]["key"]
            for row in report["results"]["recommendations"]
            if row["quality_gate_passed"] and row["cheaper_on_sample"]
        )
        prepared = confirm(
            root,
            control,
            candidate=winner,
            max_usd="5",
            max_requests=1000,
            purpose="owned-synthetic-confirmation",
        )
        assert prepared["plan"]["native_fixture"]["transport"] == "scripted_test_only"
        queued = advance(
            root,
            control,
            plan_hash=prepared["plan"]["plan_hash"],
            approval_ref="synthetic-confirmation",
        )
        result = run_one(
            api[2].state.sessions,
            api[2].state.artifact_store,
            executor=FixtureExecutor(),
            execution_enabled=True,
            only_run_id=queued["run_id"],
        )
        assert result["status"] == "COMPLETED"
        assert advance(root, control)["phase"] == "COMPLETED"
        assert not gate(root, control, candidate=winner)["passed"]
        assert gate(root, control, candidate=winner, sample_only=True)["passed"]
        artifact = proposal(root, control, purpose="owned-synthetic-diff", candidate=winner)
        assert artifact["review_status"] == "human_review_required"
        assert "gpt-4.1-mini" in artifact["diff"]
        assert "gpt-4.1" in artifact["rollback_diff"]
        assert load_session(root)["phase"] == "PROPOSAL_READY"
        saved_proposal = load_session(root)["proposal"]
        before = len(calls)
        polled = advance(root, control)
        assert polled["run_id"] == queued["run_id"]
        same_proposal = load_session(root)["proposal"] == saved_proposal
        assert same_proposal, "POLLING_REPLACED_THE_REVIEWABLE_PROPOSAL"
        assert len(calls) == before
    finally:
        native.close()
