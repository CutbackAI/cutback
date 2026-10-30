"""A fresh independent application, durable criteria review and native source proposal."""

import json
from uuid import UUID

import httpx
import pytest
from support_agent import native_response, prepare_fresh

from cutback.agent import advance, analyze, confirm, gate, load_session, proposal
from cutback.agent_criteria import prepare as prepare_criteria
from cutback.agent_criteria import read, review
from cutback.evals.executors import OpenAIExecutor
from cutback.server.criteria_worker import run_criteria_one
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


class LoseReply:
    def __init__(self, control, suffix):
        self.control, self.endpoint, self.suffix = control, control.endpoint, suffix
        self.posts = 0
        self.identity = None

    def __call__(self, method, path, body=None):
        result = self.control(method, path, body)
        if method == "POST" and path.endswith(self.suffix):
            self.posts += 1
            self.identity = result.get("id") or result.get("generation_run_id")
            if self.posts == 1:
                raise ConnectionError("OWNED_LOST_CRITERIA_REPLY")
        return result


@pytest.mark.parametrize("review_decision", ["approve", "reject"])
def test_agent_reconciles_generation_and_review_before_validation_confirmation_and_source(
    api, tenant, tmp_path, monkeypatch, review_decision
):
    root, control, _, _ = prepare_fresh(api, tenant, tmp_path, monkeypatch, search=True)
    request = {
        "requirements": {
            "task_brief": "OWNED_PRIVATE_AGENT_CRITERIA_GOAL",
            "minimum": 0.7,
            "min_test_samples": 30,
            "min_test_groups": 3,
        },
        "generator": {
            "key": "criteria-generator",
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "max_input_tokens": 24000,
            "max_output_tokens": 2048,
        },
        "budget": {"max_usd": "0.1", "max_requests": 1},
        "transport": "scripted_test_only",
    }
    lost_prepare = LoseReply(control, "/criteria/drafts")
    with pytest.raises(ConnectionError):
        prepare_criteria(root, lost_prepare, request, purpose="owned-criteria-basis")
    prepared = prepare_criteria(root, lost_prepare, request, purpose="owned-criteria-basis")
    assert prepared["phase"] == "CRITERIA_PLAN_READY" and prepared["provider_calls"] == 0
    assert lost_prepare.posts == 2 and prepared["criteria"]["id"] == lost_prepare.identity
    assert prepared["criteria"]["generation"]["existing_draft"] is True
    assert "OWNED_PRIVATE" not in json.dumps(prepared)
    changed = {**request, "requirements": {**request["requirements"], "minimum": 0.8}}
    with pytest.raises(ValueError, match="CRITERIA_GENERATION_INTENT_CHANGED"):
        prepare_criteria(root, control, changed, purpose="changed-intent")
    with pytest.raises(ValueError, match="EXACT_CRITERIA_PLAN_HASH"):
        advance(root, control, plan_hash="0" * 64, approval_ref="stale-negative")
    lost_queue = LoseReply(control, "/runs")
    with pytest.raises(ConnectionError):
        advance(
            root,
            lost_queue,
            plan_hash=prepared["criteria"]["generation"]["plan_hash"],
            approval_ref="owned-generator-budget",
        )
    queued = advance(root, lost_queue)
    assert queued["phase"] == "CRITERIA_QUEUED" and lost_queue.posts == 1
    with pytest.raises(ValueError, match="ACTIVE_WORKFLOW_MUST_BE_RECONCILED"):
        analyze(root, new_session=True)
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body["model"])
        response = native_response(request).json()
        if "Propose evaluation checks" in (body.get("instructions") or ""):
            frame = json.loads(body["input"])
            assert frame["unseen_test_rows_supplied"] is False
            response["output"][0]["content"][0]["text"] = json.dumps(
                {
                    "criteria": [
                        {
                            "description": "OWNED_PRIVATE_AGENT_CHECK",
                            "metric_kind": "exact_label",
                            "output_field": None,
                            "schema_definition": None,
                            "training_anchors": ["training-1"],
                        }
                    ]
                }
            )
        return httpx.Response(200, json=response)

    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "owned-synthetic-only")
    executor = OpenAIExecutor(
        http_client=httpx.Client(transport=httpx.MockTransport(handle), trust_env=False)
    )
    executor.synthetic_transport = True
    try:
        generation_run = queued["criteria"]["generation"]["generation_run_id"]
        assert (
            run_criteria_one(
                api[2].state.sessions,
                api[2].state.artifact_store,
                executor=executor,
                execution_enabled=True,
                only_run_id=UUID(generation_run),
            )["status"]
            == "COMPLETED"
        )
        generated = advance(root, control)
        assert generated["phase"] == "CRITERIA_REVIEW_REQUIRED" and len(calls) == 1
        with pytest.raises(ValueError, match="PURPOSE_READ"):
            review(
                root,
                control,
                version_sha256=generated["criteria"]["generation"]["version_sha256"],
                decision="approve",
                approval_ref="unread-negative",
                purpose="owned-negative",
            )
        content = read(root, control, purpose="owned-private-check-review")
        assert "OWNED_PRIVATE_AGENT_CHECK" in json.dumps(content)
        if review_decision == "reject":
            rejected = review(
                root,
                control,
                version_sha256=content["version_sha256"],
                decision="reject",
                approval_ref="owned-rejection",
                purpose="owned-private-check-review",
            )
            assert rejected["phase"] == "CRITERIA_REJECTED" and len(calls) == 1
            assert rejected["search_mode"] == "model_and_output_cap"
            assert "OWNED_PRIVATE" not in json.dumps(load_session(root))
            return
        lost_validation = LoseReply(control, "/evaluation-draft")
        with pytest.raises(ConnectionError):
            review(
                root,
                lost_validation,
                version_sha256=content["version_sha256"],
                decision="approve",
                approval_ref="owned-criteria-review",
                purpose="owned-source-basis-review",
            )
        planned = review(
            root,
            lost_validation,
            version_sha256=content["version_sha256"],
            decision="approve",
            approval_ref="owned-criteria-review",
            purpose="owned-source-basis-review",
        )
        assert planned["phase"] == "PLAN_READY" and planned["search_mode"] == "reviewed_criteria"
        assert planned["plan"]["id"] == lost_validation.identity and lost_validation.posts == 2
        assert planned["plan"]["existing_plan"] is True and len(calls) == 1
        assert "OWNED_PRIVATE" not in json.dumps(load_session(root))
        validation = advance(
            root,
            control,
            plan_hash=planned["plan"]["plan_hash"],
            approval_ref="owned-validation-budget",
        )
        assert (
            run_one(
                api[2].state.sessions,
                api[2].state.artifact_store,
                executor=executor,
                execution_enabled=True,
                only_run_id=validation["run_id"],
            )["status"]
            == "COMPLETED"
        )
        completed = advance(root, control)
        assert completed["next_action"]["code"] == "SELECT_CANDIDATE_AND_FREEZE_CONFIRMATION"
        assert gate(root, control, sample_only=True)["passed"] is False
        selected = next(
            row["candidate"]["key"]
            for row in completed["decisions"]
            if row["quality_gate_passed"] and row["cheaper_on_sample"]
        )
        lost_confirmation = LoseReply(control, "/experiments/plans")
        with pytest.raises(ConnectionError):
            confirm(
                root,
                lost_confirmation,
                candidate=selected,
                max_usd="5",
                max_requests=1000,
                purpose="owned-confirmation",
            )
        with pytest.raises(ValueError, match="FROZEN_CONFIRMATION_INTENT_CHANGED"):
            confirm(
                root,
                control,
                candidate=selected,
                max_usd="6",
                max_requests=1000,
                purpose="changed-confirmation",
            )
        confirmed = confirm(
            root,
            lost_confirmation,
            candidate=selected,
            max_usd="5",
            max_requests=1000,
            purpose="owned-confirmation",
        )
        assert (
            lost_confirmation.posts == 2 and confirmed["plan"]["id"] == lost_confirmation.identity
        )
        queued_test = advance(
            root,
            control,
            plan_hash=confirmed["plan"]["plan_hash"],
            approval_ref="owned-confirmation-budget",
        )
        assert (
            run_one(
                api[2].state.sessions,
                api[2].state.artifact_store,
                executor=executor,
                execution_enabled=True,
                only_run_id=queued_test["run_id"],
            )["status"]
            == "COMPLETED"
        )
        assert advance(root, control)["phase"] == "COMPLETED"
        before = (root / "app.py").read_bytes()
        artifact = proposal(root, control, purpose="owned-source-review", candidate=selected)
        assert artifact["status"] == "DRY_RUN_ONLY" and artifact["rollback_diff"]
        assert (root / "app.py").read_bytes() == before
        assert "OWNED_PRIVATE" not in json.dumps(load_session(root))
        validity = control(
            "GET",
            f"/v1/projects/{planned['project_id']}/criteria/{prepared['criteria']['id']}"
            + f"/evidence-validity?run_id={queued_test['run_id']}",
        )
        assert validity["eligible"] is True and validity["historical_quality_recomputed"] is False
        current = control(
            "GET", f"/v1/projects/{planned['project_id']}/criteria/{prepared['criteria']['id']}"
        )
        control(
            "POST",
            f"/v1/projects/{planned['project_id']}/criteria/{prepared['criteria']['id']}/review",
            {
                "version_sha256": content["version_sha256"],
                "decision": "reject",
                "approval_ref": "owned-withdrawal",
                "expected_review_approval_id": current["review_approval_id"],
            },
        )
        assert gate(root, control, candidate=selected, sample_only=True)["reasons"] == [
            "current_reviewed_confirmation_unavailable"
        ]
        with pytest.raises(
            ValueError, match="CRITERIA_REVIEWED_VERSION_UNAVAILABLE_OR_NOT_APPROVED"
        ):
            proposal(root, control, purpose="withdrawn-checks", candidate=selected)
    finally:
        executor.close()
