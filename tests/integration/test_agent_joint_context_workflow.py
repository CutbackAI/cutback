"""Fresh application, durable agent resume, native transports and combined proposal."""

import json
import os
from uuid import UUID

import pytest
from support_compression_application import application
from support_retrieval_application import git

from cutback.agent import advance, analyze, confirm, gate, load_session, prepare, proposal
from cutback.agent_control import Control
from cutback.demo_compression import synthetic_executor
from cutback.evals.artifacts import EncryptedFileStore
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("automatic", [False, True])
def test_joint_agent_prepares_once_reconciles_lost_queue_and_reviews_exact_source(
    api, tenant, tmp_path, monkeypatch, automatic
):
    root = application(tmp_path)
    path = root / "app.py"
    path.write_bytes(path.read_bytes().replace(b'model="gpt-4.1-mini"', b'model="gpt-4.1"'))
    git(root, "add", ".")
    git(root, "commit", "-m", "Owned higher-cost application baseline")
    analyzed = analyze(root)
    workflow = analyzed["context_producers"][0]["workflow_id"]
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    control = Control("http://127.0.0.1", api[1]["Authorization"][7:], client=api[0])
    args = dict(
        files=["questions.jsonl"],
        rights_basis="synthetic",
        rights_ref="owned-context-agent",
        max_usd="1",
        max_requests=200,
        workflow=workflow,
        minimum=0.9,
        independent_labels=True,
        joint_context=not automatic,
        automatic=automatic,
        assumed_usd_per_cpu_hour="0.01",
        min_test_samples=3,
    )
    state = prepare(root, control, tenant[0], **args)
    assert state["phase"] == "PLAN_READY" and state["plan"]["paid_calls_made"] == 0
    assert prepare(root, control, tenant[0], **args)["plan"] == state["plan"]
    with pytest.raises(ValueError, match="FROZEN_PLAN_CHANGED"):
        prepare(root, control, tenant[0], **{**args, "max_usd": "2"})
    assert "deadline-days" not in json.dumps(load_session(root))

    class LostQueue:
        endpoint = control.endpoint
        queues = 0

        def __call__(self, method, path, body=None):
            result = control(method, path, body)
            if method == "POST" and path.endswith("/experiments"):
                self.queues += 1
                raise ConnectionError("owned lost queue acknowledgement")
            return result

    lost = LostQueue()
    with pytest.raises(ConnectionError):
        advance(
            root, lost, plan_hash=state["plan"]["plan_hash"], approval_ref="owned-synthetic-budget"
        )
    queued = advance(root, lost)
    assert lost.queues == 1 and advance(root, lost)["run_id"] == queued["run_id"]
    executor, calls = synthetic_executor(joint=True)
    try:
        result = run_one(
            api[2].state.sessions,
            api[2].state.artifact_store,
            executor=executor,
            execution_enabled=True,
            only_run_id=UUID(queued["run_id"]),
        )
        assert result["status"] == "COMPLETED", result
        completed = advance(root, control)
        assert completed["next_action"]["code"] == "SELECT_CANDIDATE_AND_FREEZE_CONFIRMATION"
        assert not gate(root, control, sample_only=True)["passed"]
        with pytest.raises(ValueError):
            proposal(root, control, purpose="owned-unconfirmed-review")
        selected = next(
            row["candidate"]["key"]
            for row in completed["decisions"]
            if row["joint_model_context_confirmation_eligible"]
        )
        confirmation = confirm(
            root,
            control,
            candidate=selected,
            max_usd="1",
            max_requests=100,
            purpose="owned-confirmation",
        )
        assert (
            confirmation["phase"] == "PLAN_READY"
            and confirmation["plan"]["plan_hash"] != state["plan"]["plan_hash"]
        )
        queued = advance(
            root,
            control,
            plan_hash=confirmation["plan"]["plan_hash"],
            approval_ref="owned-untouched-confirmation",
        )
        result = run_one(
            api[2].state.sessions,
            api[2].state.artifact_store,
            executor=executor,
            execution_enabled=True,
            only_run_id=UUID(queued["run_id"]),
        )
        assert result["status"] == "COMPLETED", result
        completed = advance(root, control)
        assert completed["next_action"]["code"] == "REVIEW_EVIDENCE_AND_CHANGE"
        assert gate(root, control, sample_only=True)["passed"]
        artifact = proposal(root, control, purpose="owned-confirmed-review")
        assert set(artifact["proposed_files"]) == {"app.py", "compression.json"}
        assert len(calls) > 0 and path.read_bytes().find(b'model="gpt-4.1"') >= 0
        assert load_session(root)["phase"] == "PROPOSAL_READY"
    finally:
        executor.close()
