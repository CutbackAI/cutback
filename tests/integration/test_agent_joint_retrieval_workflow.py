"""Joint source workflow: canonical evidence, recoverable budgets and native consumer checks."""

import os
from uuid import UUID

import pytest
from support_retrieval_application import application, git

from cutback.agent import advance, analyze, confirm, gate, load_session, prepare, proposal
from cutback.agent_control import Control
from cutback.application_check_discovery import discover
from cutback.application_checks import ApplicationCheckContract, run_checks
from cutback.demo_retrieval import synthetic_executor
from cutback.evals.artifacts import EncryptedFileStore
from cutback.joint_retrieval_changes import review_change
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


def setup(api, tenant, tmp_path, monkeypatch, *, cpu="0.01"):
    root = application(tmp_path)
    path = root / "app.py"
    path.write_text(path.read_text().replace('model="gpt-4.1-mini"', 'model="gpt-4.1"'))
    git(root, "add", ".")
    git(root, "commit", "-m", "Qualified synthetic baseline")
    analyze(root)
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    control = Control("http://127.0.0.1", api[1]["Authorization"][7:], client=api[0])
    arguments = dict(
        files=["questions.jsonl"],
        rights_basis="synthetic",
        rights_ref="owned-joint-retrieval",
        max_usd="5",
        max_requests=500,
        minimum=1,
        independent_labels=True,
        representative=False,
        assumed_usd_per_cpu_hour=cpu,
        min_test_samples=3,
        automatic=True,
        retrieval_corpus_authorized=True,
    )
    return root, control, arguments


def execute(api, root, control, state, executor):
    queued = advance(
        root, control, plan_hash=state["plan"]["plan_hash"], approval_ref="owned-synthetic"
    )
    result = run_one(
        api[2].state.sessions,
        api[2].state.artifact_store,
        executor=executor,
        execution_enabled=True,
        only_run_id=UUID(queued["run_id"]),
    )
    assert result["status"] == "COMPLETED", result
    return advance(root, control)


def test_joint_confirmation_budget_recovery_exact_both_file_change_and_rollback(
    api, tenant, tmp_path, monkeypatch
):
    root, control, arguments = setup(api, tenant, tmp_path, monkeypatch)
    original = {name: (root / name).read_bytes() for name in ("app.py", "retrieval.json")}
    prepared = prepare(root, control, tenant[0], **arguments)
    assert load_session(root)["search_mode"] == "joint_model_retrieval"
    executor, calls = synthetic_executor()
    try:
        validated = execute(api, root, control, prepared, executor)
        selected = next(
            row["candidate"]["key"]
            for row in validated["decisions"]
            if row["joint_model_retrieval_confirmation_eligible"]
            and "native_model" in row["search_axes_changed"]
            and "retrieval_settings" in row["search_axes_changed"]
        )
        assert not gate(root, control, sample_only=True)["passed"]
        with pytest.raises(ValueError):
            proposal(root, control, purpose="owned-unconfirmed")
        legacy = api[0].post(
            f"/v1/projects/{tenant[0]}/retrieval/confirmation",
            headers=api[1],
            json={
                "validation_run_id": validated["run_id"],
                "budget": {"max_usd": "5", "max_requests": 100},
            },
        )
        assert legacy.status_code == 409
        with pytest.raises(ValueError, match="CONFIRMATION_EXCEEDS_BUDGET"):
            confirm(
                root,
                control,
                candidate=selected,
                max_usd="0.000000000001",
                max_requests=100,
                purpose="owned-confirmation",
            )
        assert load_session(root)["phase"] == "COMPLETED"
        assert not load_session(root).get("confirmation")
        confirmation = confirm(
            root,
            control,
            candidate=selected,
            max_usd="5",
            max_requests=100,
            purpose="owned-confirmation",
        )
        assert (
            confirm(
                root,
                control,
                candidate=selected,
                max_usd="5",
                max_requests=100,
                purpose="owned-confirmation",
            )["plan"]
            == confirmation["plan"]
        )
        completed = execute(api, root, control, confirmation, executor)
        assert completed["run_id"] != validated["run_id"]
        assert gate(root, control, sample_only=True)["passed"]
        assert not gate(root, control)["passed"]  # Synthetic transport cannot verify real quality.
        artifact = proposal(root, control, purpose="owned-source-review", candidate=selected)
        path = f"/v1/projects/{tenant[0]}/model-retrieval/{completed['run_id']}/proposal"
        assert (
            api[0].post(path, headers=tenant[2], json={"purpose": "owned-viewer"}).status_code
            == 403
        )
        assert (
            api[0].post(path, headers=tenant[1], json={"purpose": "owned-ingest"}).status_code
            == 403
        )
        legacy_proposal = path.replace("/model-retrieval/", "/retrieval/")
        assert (
            api[0]
            .post(legacy_proposal, headers=api[1], json={"purpose": "owned-wrong-contract"})
            .status_code
            == 409
        )
        # Revocation prevents a later purpose-bound read even after successful confirmation.
        policy = load_session(root)["policy_id"]
        revoked = api[0].delete(
            f"/v1/projects/{tenant[0]}/capture-policies/{policy}", headers=api[1]
        )
        assert revoked.status_code == 200
        assert (
            api[0].post(path, headers=api[1], json={"purpose": "owned-revoked"}).status_code == 403
        )
    finally:
        executor.close()
    assert calls and set(artifact["proposed_files"]) == {"app.py", "retrieval.json"}
    assert all((root / name).read_bytes() == value for name, value in original.items())
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
        "Review joint change",
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
        branch="codex/owned-joint-review",
    )
    assert review["status"] == "READY_FOR_LOCAL_REVIEW" and not review["production_deployed"]
    patch.write_text(artifact["rollback_diff"], encoding="utf-8", newline="")
    git(proposed, "apply", str(patch))
    assert all((proposed / name).read_bytes() == value for name, value in original.items())


def test_joint_recipe_upgrade_stops_before_any_mutation(api, tenant, tmp_path, monkeypatch):
    root, control, arguments = setup(api, tenant, tmp_path, monkeypatch)
    writes = []

    class OldCollector:
        endpoint = control.endpoint

        def __call__(self, method, path, body=None):
            if method == "GET" and path == "/v1/health/ready":
                return {**control(method, path, body), "retrieval_recipe_sha256": "0" * 64}
            if method != "GET":
                writes.append(path)
            return control(method, path, body)

    with pytest.raises(ValueError, match="COLLECTOR_JOINT_RETRIEVAL_UPGRADE_REQUIRED"):
        prepare(root, OldCollector(), tenant[0], **arguments)
    assert not writes


def test_joint_unknown_cpu_cannot_confirm(api, tenant, tmp_path, monkeypatch):
    root, control, arguments = setup(api, tenant, tmp_path, monkeypatch, cpu=None)
    prepared = prepare(root, control, tenant[0], **arguments)
    executor, _ = synthetic_executor()
    try:
        validated = execute(api, root, control, prepared, executor)
    finally:
        executor.close()
    assert all(
        not row["joint_model_retrieval_confirmation_eligible"] for row in validated["decisions"]
    )
    with pytest.raises(ValueError, match="NO_JOINT_RETRIEVAL_CANDIDATE"):
        confirm(
            root,
            control,
            candidate=validated["decisions"][0]["candidate"]["key"],
            max_usd="5",
            max_requests=100,
            purpose="owned-unknown",
        )
