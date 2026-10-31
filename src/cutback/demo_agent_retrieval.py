"""Independent agent retrieval journey using an owned native synthetic transport."""

import json
import shutil
from pathlib import Path
from uuid import UUID, uuid4


def seed_agent_retrieval(app, control, project_id, workspace, *, joint=False):
    from cutback.agent import advance, analyze, confirm, gate, prepare, proposal
    from cutback.agent_control import Control
    from cutback.application_check_discovery import discover
    from cutback.application_checks import ApplicationCheckContract, git, run_checks
    from cutback.core.retrieval import RetrievalSettings
    from cutback.core.workspace import private_directory
    from cutback.demo_retrieval import synthetic_executor
    from cutback.sample_app.retrieval_fixture import fixture
    from cutback.server.worker import run_one

    workspace.mkdir(parents=True, exist_ok=True)
    root = private_directory(workspace, "agent-retrieval-" + uuid4().hex)
    shutil.copyfile(Path(__file__).parent / "sample_app/retrieval.py", root / "app.py")
    if joint:
        source = (root / "app.py").read_text(encoding="utf-8")
        (root / "app.py").write_text(
            source.replace('model="gpt-4.1-mini"', 'model="gpt-4.1"'), encoding="utf-8"
        )
    corpus, cases = fixture()
    (root / "corpus.json").write_text(json.dumps(corpus) + "\n", encoding="utf-8")
    (root / "questions.jsonl").write_text(
        "".join(json.dumps(case) + "\n" for case in cases), encoding="utf-8"
    )
    (root / "retrieval.json").write_text(
        RetrievalSettings(chunk_chars=512, overlap_chars=0, top_k=1).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
    )
    (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    (root / "check_application.py").write_text(
        "from app import build_input\n\ndef main():\n"
        '    frame = build_input("refund return deadline for amber")\n'
        '    assert "deadline-days=14" in frame\n'
        "    assert len(frame.encode()) < 16000\n\n"
        'if __name__ == "__main__":\n    main()\n',
        encoding="utf-8",
    )
    if joint:
        shutil.copyfile(
            Path(__file__).parent / "sample_app/retrieval_check.py", root / "check_application.py"
        )
    git(root, "init")
    git(root, "config", "core.autocrlf", "false")
    git(root, "add", ".")

    def commit(directory, message):
        git(
            directory,
            "-c",
            "user.name=Synthetic Cutback Sample",
            "-c",
            "user.email=synthetic@example.invalid",
            "commit",
            "-m",
            message,
        )

    commit(root, "Independent synthetic corpus application")
    analyzed = analyze(root)
    adapter = Control(str(control.base_url).rstrip("/"), "", client=control)
    # Keep the already scoped caller credential in memory, never in the journal.
    authorization = control.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise ValueError("SYNTHETIC_RETRIEVAL_SCOPED_CONTROL_REQUIRED")
    adapter.token = authorization[7:]
    prepared = prepare(
        root,
        adapter,
        project_id,
        files=["questions.jsonl"],
        rights_basis="synthetic",
        rights_ref="owned-agent-retrieval-corpus",
        max_usd="5",
        max_requests=500,
        automatic=joint,
        retrieval=not joint,
        retrieval_corpus_authorized=True,
        minimum=1,
        independent_labels=True,
        representative=False,
        assumed_usd_per_cpu_hour="0.01",
        min_test_samples=3,
    )
    executor, calls = synthetic_executor()

    def execute(state, approval):
        queued = advance(root, adapter, plan_hash=state["plan"]["plan_hash"], approval_ref=approval)
        result = run_one(
            app.state.sessions,
            app.state.artifact_store,
            executor=executor,
            execution_enabled=True,
            only_run_id=UUID(queued["run_id"]),
        )
        if result["status"] != "COMPLETED":
            raise ValueError("SYNTHETIC_AGENT_RETRIEVAL_RUN_FAILED")
        return advance(root, adapter)

    try:
        validated = execute(prepared, "owned-synthetic-validation")
        selected = next(
            (
                row["candidate"]["key"]
                for row in validated["decisions"]
                if row.get("retrieval_automation_eligible") is True
                and (not joint or len(row.get("search_axes_changed", [])) >= 2)
            ),
            None,
        )
        if selected is None:
            raise ValueError("SYNTHETIC_AGENT_RETRIEVAL_NO_ELIGIBLE_CANDIDATE")
        confirmation = confirm(
            root,
            adapter,
            candidate=selected,
            max_usd="5",
            max_requests=100,
            purpose="owned-synthetic-confirmation",
        )
        completed = execute(confirmation, "owned-synthetic-untouched-confirmation")
        if not gate(root, adapter, sample_only=True)["passed"]:
            raise ValueError("SYNTHETIC_AGENT_RETRIEVAL_CONFIRMATION_FAILED")
        artifact = proposal(root, adapter, purpose="owned-synthetic-source-review")
    finally:
        executor.close()
    proposed = root.parent / (root.name + "-proposed")
    # Both repositories are this function's own finite synthetic source. Copying
    # its local Git database needs no transport or permission to access remotes.
    shutil.copytree(root, proposed, ignore=shutil.ignore_patterns(".cutback", "__pycache__"))
    patch = root.parent / (root.name + ".patch")
    patch.write_text(artifact["diff"], encoding="utf-8", newline="")
    git(proposed, "apply", str(patch))
    git(proposed, "add", ".")
    commit(proposed, "Review retrieval settings")
    contract = ApplicationCheckContract.model_validate(discover(root, proposed)["contract"])
    receipt = run_checks(contract, root, proposed, execute_local=True)
    if not receipt["all_passed"]:
        raise ValueError("SYNTHETIC_AGENT_RETRIEVAL_APPLICATION_CHECK_FAILED")
    if joint:
        from cutback.joint_retrieval_changes import review_change
    else:
        from cutback.retrieval_changes import review_change
    review = review_change(
        artifact,
        contract.model_dump(mode="json"),
        receipt,
        root,
        proposed,
        {analyzed["retrieval_producers"][0]["workflow_id"]: [c.key for c in contract.commands]},
        branch="codex/synthetic-retrieval-review",
    )
    patch.write_text(artifact["rollback_diff"], encoding="utf-8", newline="")
    git(proposed, "apply", str(patch))
    if any(
        (proposed / name).read_bytes() != (root / name).read_bytes()
        for name in ("app.py", "retrieval.json")
    ):
        raise ValueError("SYNTHETIC_AGENT_RETRIEVAL_ROLLBACK_MISMATCH")
    return {
        "dataset_id": prepared["dataset_id"],
        "validation_run_id": validated["run_id"],
        "confirmation_run_id": completed["run_id"],
        "native_sdk_calls": len(calls),
        **(
            {
                "local_consumer_check_http_calls": 4,
                "search_axes": ["model", "output_cap", "retrieval"],
            }
            if joint
            else {}
        ),
        "paid_provider_calls": 0,
        "review_status": review["status"],
        "rollback_verified": True,
        "application_changed": False,
        "production_deployed": False,
        "source_change": artifact,
    }
