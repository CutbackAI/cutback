"""Real collector/PG, native SDK receipts and resumable source-bound routing automation."""

import json
import os
from uuid import UUID

import pytest
from support_routing_application import application

from cutback.agent import advance, analyze, load_session, prepare, proposal
from cutback.agent_control import Control
from cutback.demo_routing import synthetic_executor
from cutback.evals.artifacts import EncryptedFileStore
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("automatic", [False, True])
def test_agent_routes_automatically_between_separately_approved_stages_without_replay(
    api, tenant, tmp_path, monkeypatch, automatic
):
    root = application(tmp_path)
    analyzed = analyze(root)
    assert len(analyzed["routing_producers"]) == 1
    assert analyzed["next_action"]["code"] == "PREPARE_SOURCE_BOUND_ROUTING"
    workflow = analyzed["routing_producers"][0]["workflow_id"]
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    control = Control("http://127.0.0.1", api[1]["Authorization"][7:], client=api[0])
    args = dict(
        files=["tickets.jsonl"],
        rights_basis="synthetic",
        rights_ref="owned-routing-agent",
        max_usd="4",
        max_requests=500,
        workflow=workflow,
        minimum=1,
        independent_labels=True,
        representative=False,
        learned_routing=not automatic,
        automatic=automatic,
        fixture_transport=True,
        min_test_samples=3,
    )
    state = prepare(root, control, tenant[0], **args)
    assert state["phase"] == "PLAN_READY" and state["routing"]["stage"] == "training"
    assert state["plan"]["paid_calls_made"] == 0
    assert prepare(root, control, tenant[0], **args)["plan"] == state["plan"]
    assert "Classify the declared" not in json.dumps(load_session(root))
    with pytest.raises(ValueError, match="FROZEN_ROUTING_INTENT_CHANGED"):
        prepare(root, control, tenant[0], **{**args, "minimum": 0.9})

    class LoseReplies:
        endpoint = control.endpoint

        def __init__(self):
            self.lost = set()
            self.queues = 0

        def __call__(self, method, path, body=None):
            result = control(method, path, body)
            if method == "POST" and path.endswith("/experiments"):
                self.queues += 1
            kind = (
                "queue"
                if method == "POST" and path.endswith("/experiments")
                else "fit"
                if method == "POST" and path.endswith("/routing/fits")
                else "validation"
                if path.endswith("/validation-drafts")
                else None
            )
            if kind and kind not in self.lost:
                self.lost.add(kind)
                raise ConnectionError("Owned lost non-replayed response")
            return result

    lost = LoseReplies()
    executor, calls = synthetic_executor()
    hashes = []
    try:
        for stage in ["training", "validation", "confirmation"]:
            state = load_session(root)
            assert state["phase"] == "PLAN_READY" and state["routing"]["stage"] == stage
            hashes.append(state["plan"]["plan_hash"])
            with pytest.raises(ValueError, match="EXACT_PLAN_HASH"):
                advance(root, lost)
            try:
                queued = advance(root, lost, plan_hash=hashes[-1], approval_ref="owned-" + stage)
            except ConnectionError:
                assert stage == "training"
                queued = advance(root, lost)
            assert queued["phase"] == "QUEUED"
            count = len(calls)
            assert advance(root, lost)["run_id"] == queued["run_id"] and len(calls) == count
            result = run_one(
                api[2].state.sessions,
                api[2].state.artifact_store,
                executor=executor,
                execution_enabled=True,
                only_run_id=UUID(queued["run_id"]),
            )
            assert result["status"] == "COMPLETED", result.get("results", {}).get("code")
            if stage == "confirmation":
                assert result["results"]["billing_complete"] is True
                assert result["results"]["lineage"]["fresh_test_claim"] is True
                decision = result["results"]["recommendations"][0]
                from cutback.evals.datasets import digest
                from cutback.server.plan_artifacts import public_config

                frozen = control(
                    "POST",
                    f"/v1/projects/{tenant[0]}/experiments/plans/{state['plan']['id']}/content",
                    {"purpose": "owned-exact-policy-check"},
                )
                assert digest(decision["candidate"]) == digest(
                    public_config(
                        frozen["specification"]["candidates"][0], redact_content_hashes=True
                    )
                )
                assert decision["quality_gate_passed"] and decision["cheaper_on_sample"], {
                    key: decision.get(key)
                    for key in (
                        "quality_gate_passed",
                        "cheaper_on_sample",
                        "decision_reason",
                        "sample_n",
                        "failed_criteria",
                        "insufficient_criteria",
                    )
                }
            for _ in range(3):
                try:
                    state = advance(root, lost)
                    break
                except ConnectionError:
                    continue
            else:
                pytest.fail("Stable fit/preparation recovery did not complete")
            assert state["phase"] == ("COMPLETED" if stage == "confirmation" else "PLAN_READY")
        assert len(set(hashes)) == 3 and lost.queues == 3
        artifact = proposal(root, control, purpose="owned-final-source-review")
        assert artifact["proposal"]["decision"] == "synthetic_local_review_only"
        assert artifact["proposal"]["full_task_cost_known"] is False
        assert artifact["applied"] is False and "routing.json" in artifact["diff"]
        count = len(calls)
        assert proposal(root, control, purpose="owned-final-source-review") == artifact
        assert len(calls) == count
        policy_id = load_session(root)["policy_id"]
        control("DELETE", f"/v1/projects/{tenant[0]}/capture-policies/{policy_id}")
        with pytest.raises(ValueError):
            proposal(root, control, purpose="owned-revoked-source-review")
        assert len(calls) == count
    finally:
        executor.close()
