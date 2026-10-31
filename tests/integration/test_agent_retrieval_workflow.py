"""Independent retrieval discovery/preparation must respect corpus rights and durable setup."""

import json
import os
from uuid import UUID

import pytest
from support_retrieval_application import application, git

from cutback.agent import advance, analyze, confirm, gate, load_session, prepare, proposal
from cutback.agent_control import Control
from cutback.demo_retrieval import synthetic_executor
from cutback.evals.artifacts import EncryptedFileStore
from cutback.server.db import ExperimentRunRecord
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


def options(**changes):
    return (
        dict(
            files=["questions.jsonl"],
            rights_basis="synthetic",
            rights_ref="owned-retrieval",
            max_usd="5",
            max_requests=500,
            minimum=1,
            independent_labels=True,
            representative=False,
            assumed_usd_per_cpu_hour="3.6",
            min_test_samples=3,
            retrieval=True,
            retrieval_corpus_authorized=True,
        )
        | changes
    )


def test_discovery_and_preparation_freeze_actual_corpus_without_inference(
    api, tenant, tmp_path, monkeypatch
):
    root = application(tmp_path)
    original = {
        name: (root / name).read_bytes() for name in ("app.py", "retrieval.json", "corpus.json")
    }
    state = analyze(root)
    assert len(state["retrieval_producers"]) == 1
    assert state["preparation_options"][0]["modes"] == ["joint_model_retrieval"]
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    control = Control("http://127.0.0.1", api[1]["Authorization"][7:], client=api[0])
    prepared = prepare(root, control, tenant[0], **options())
    assert prepared["phase"] == "PLAN_READY"
    session = load_session(root)
    assert session["search_mode"] == "retrieval_search"
    content = control(
        "POST",
        f"/v1/projects/{tenant[0]}/experiments/plans/{session['plan']['id']}/content",
        {"purpose": "owned-retrieval-plan-check"},
    )["specification"]
    corpus = json.loads(original["corpus.json"])
    assert content["retrieval_execution"]["corpus"] == corpus
    assert content["evaluator_version"] == "deterministic-v6"
    assert len(content["retrieval_execution"]["variants"]) >= 2
    assert content["split"] == "validation" and content["content_nonce"]
    assert corpus["documents"][0]["text"] not in json.dumps(session)
    assert (
        prepare(root, control, tenant[0], **options())["plan"]["plan_hash"]
        == session["plan"]["plan_hash"]
    )
    with api[2].state.sessions() as database:
        assert (
            database.query(ExperimentRunRecord).filter_by(project_id=UUID(tenant[0])).count() == 0
        )
    assert all((root / name).read_bytes() == raw for name, raw in original.items())


def test_missing_corpus_authorization_stops_before_any_collector_request(tmp_path):
    root = application(tmp_path)
    analyze(root)
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        pytest.fail("NO_COLLECTOR_REQUEST_AUTHORIZED")

    parameters = options()
    parameters["retrieval_corpus_authorized"] = False
    with pytest.raises(ValueError, match="EXPLICIT_BOUND_RETRIEVAL_CORPUS_AUTHORIZATION_REQUIRED"):
        prepare(root, forbidden, "unused", **parameters)
    assert not calls and load_session(root)["phase"] == "ANALYZED"


@pytest.mark.parametrize("automatic", [False, True])
def test_retrieval_agent_reconciles_queue_confirms_and_prepares_source_review(
    api, tenant, tmp_path, monkeypatch, automatic
):
    root = application(tmp_path)
    original = (root / "retrieval.json").read_bytes()
    analyze(root)
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    control = Control("http://127.0.0.1", api[1]["Authorization"][7:], client=api[0])
    prepared = prepare(
        root,
        control,
        tenant[0],
        **options(retrieval=not automatic, automatic=automatic, assumed_usd_per_cpu_hour="0.01"),
    )

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
        advance(root, lost, plan_hash=prepared["plan"]["plan_hash"], approval_ref="owned-budget")
    queued = advance(root, lost)
    assert advance(root, lost)["run_id"] == queued["run_id"] and lost.queues == 1
    executor, calls = synthetic_executor()
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
            proposal(root, control, purpose="owned-unconfirmed")
        selected = next(
            row["candidate"]["key"]
            for row in completed["decisions"]
            if row["retrieval_automation_eligible"]
        )

        class LostConfirmation:
            endpoint = control.endpoint
            writes = 0

            def __call__(self, method, path, body=None):
                result = control(method, path, body)
                if method == "POST" and path.endswith("/experiments/plans"):
                    self.writes += 1
                    if self.writes == 1:
                        raise ConnectionError("owned lost nonbillable confirmation reply")
                return result

        confirmation_control = LostConfirmation() if automatic else control
        if automatic:
            with pytest.raises(ConnectionError):
                confirm(
                    root,
                    confirmation_control,
                    candidate=selected,
                    max_usd="5",
                    max_requests=100,
                    purpose="owned-independent-confirmation",
                )
            assert load_session(root)["phase"] == "PREPARING_CONFIRMATION"
            with pytest.raises(ValueError, match="FROZEN_CONFIRMATION_INTENT_CHANGED"):
                confirm(
                    root,
                    confirmation_control,
                    candidate=selected,
                    max_usd="6",
                    max_requests=100,
                    purpose="owned-independent-confirmation",
                )
        confirmation = confirm(
            root,
            confirmation_control,
            candidate=selected,
            max_usd="5",
            max_requests=100,
            purpose="owned-independent-confirmation",
        )
        assert confirmation["plan"]["plan_hash"] != prepared["plan"]["plan_hash"]
        queued = advance(
            root,
            control,
            plan_hash=confirmation["plan"]["plan_hash"],
            approval_ref="owned-confirmation-budget",
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
        checked = gate(root, control, sample_only=True)
        assert checked["passed"], checked
        artifact = proposal(root, control, purpose="owned-confirmed-review", candidate=selected)
        assert artifact["version"] == (
            "cutback.joint-retrieval-change.v1" if automatic else "cutback.retrieval-change.v1"
        )
        assert artifact["proposal"]["binding"]["file"] == "retrieval.json"
        assert artifact["rollback_diff"] and not artifact["application_changed"]
        assert (root / "retrieval.json").read_bytes() == original
        assert calls and load_session(root)["phase"] == "PROPOSAL_READY"
        if not automatic:
            from cutback.application_check_discovery import discover
            from cutback.application_checks import ApplicationCheckContract, run_checks
            from cutback.retrieval_changes import review_change

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
                "Review retrieval",
            )
            contract = ApplicationCheckContract.model_validate(discover(root, proposed)["contract"])
            receipt = run_checks(contract, root, proposed, execute_local=True)
            assert receipt["all_passed"], receipt["status"]
            review = review_change(
                artifact,
                contract.model_dump(mode="json"),
                receipt,
                root,
                proposed,
                {"support-rag": [command.key for command in contract.commands]},
                branch="codex/owned-retrieval-review",
            )
            assert review["status"] == "READY_FOR_LOCAL_REVIEW"
            patch.write_text(artifact["rollback_diff"], encoding="utf-8", newline="")
            git(proposed, "apply", str(patch))
            assert (proposed / "retrieval.json").read_bytes() == original
    finally:
        executor.close()


@pytest.mark.parametrize("resource", ["datasets", "experiments/plans"])
def test_lost_preparation_reuses_same_encrypted_objects_and_no_run(
    api, tenant, tmp_path, monkeypatch, resource
):
    from cutback.agent_preparation import resume

    root = application(tmp_path)
    analyze(root)
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    control = Control("http://127.0.0.1", api[1]["Authorization"][7:], client=api[0])

    class LostPreparation:
        endpoint = control.endpoint
        lost = False

        def __call__(self, method, path, body=None):
            value = control(method, path, body)
            if (
                method == "POST"
                and path.split("?", 1)[0].endswith("/" + resource)
                and not self.lost
            ):
                self.lost = True
                raise ConnectionError("owned lost nonbillable preparation reply")
            return value

    lost = LostPreparation()
    with pytest.raises(ConnectionError):
        prepare(root, lost, tenant[0], **options())
    frozen = load_session(root)["preparation_intent"]
    ready = resume(root, lost)
    assert ready["phase"] == "PLAN_READY"
    assert ready["plan"]["id"] == frozen["requests"]["plan"]
    assert load_session(root)["preparation_intent"] == frozen
    with api[2].state.sessions() as database:
        assert (
            database.query(ExperimentRunRecord).filter_by(project_id=UUID(tenant[0])).count() == 0
        )


def test_unknown_cpu_cost_and_revoked_corpus_cannot_authorize_confirmation(
    api, tenant, tmp_path, monkeypatch
):
    root = application(tmp_path)
    analyze(root)
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    control = Control("http://127.0.0.1", api[1]["Authorization"][7:], client=api[0])
    prepared = prepare(root, control, tenant[0], **options(assumed_usd_per_cpu_hour=None))
    queued = advance(root, control, plan_hash=prepared["plan"]["plan_hash"], approval_ref="owned")
    executor, calls = synthetic_executor()
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
        assert all(row["cheaper_on_sample"] is None for row in completed["decisions"])
        assert completed["search_results"]["eligible_candidates"] == 0
        with pytest.raises(ValueError, match="RETRIEVAL_COMPONENT_COST_AND_QUALITY_GATES_REQUIRED"):
            confirm(
                root,
                control,
                candidate=completed["decisions"][0]["candidate"]["key"],
                max_usd="5",
                purpose="owned-unknown-cost",
            )
        response = api[0].delete(
            f"/v1/projects/{tenant[0]}/capture-policies/{load_session(root)['policy_id']}",
            headers=api[1],
        )
        assert response.status_code == 200
        with pytest.raises(ValueError):
            proposal(root, control, purpose="owned-revoked-corpus")
        assert calls
    finally:
        executor.close()
