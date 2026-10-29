"""Owned synthetic corpus -> actual SDK parser -> PostgreSQL -> confirmation -> source diff.

All replies and token counts are synthetic. No inference endpoint is contacted.
"""

import json
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4


def synthetic_executor():
    import httpx

    from cutback.evals.executors import OpenAIExecutor

    calls = []

    def handle(request):
        if str(request.url) != "https://api.openai.com/v1/responses":
            raise ValueError("SYNTHETIC_RETRIEVAL_DESTINATION_REFUSED")
        body = json.loads(request.content)
        if body.get("store") is not False:
            raise ValueError("SYNTHETIC_RETRIEVAL_STORAGE_POLICY_REFUSED")
        text = body["input"]
        frame = json.loads(text[text.index("{") :])
        calls.append(len(text.encode()))
        facts = [re.search(r"\bdeadline-days=(\d+)\b", d["text"]) for d in frame["documents"]]
        answer = next((m.group(1) for m in facts if m), "unknown")
        count = len(text.encode())  # Explicit synthetic usage; not a real tokenizer measurement.
        return httpx.Response(
            200,
            json={
                "id": "resp_owned_" + uuid4().hex,
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": body["model"],
                "output": [
                    {
                        "type": "message",
                        "id": "msg_owned",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": answer, "annotations": []}],
                    }
                ],
                "usage": {
                    "input_tokens": count,
                    "output_tokens": 2,
                    "total_tokens": count + 2,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    with patch.dict("os.environ", {"CUTBACK_EXPERIMENT_OPENAI_KEY": "synthetic-no-provider-key"}):
        executor = OpenAIExecutor(
            http_client=httpx.Client(
                transport=httpx.MockTransport(handle), trust_env=False, follow_redirects=False
            )
        )
    executor.synthetic_transport = True
    return executor, calls


def seed_retrieval(app, control, project_id, workspace, *, execute=True):
    from cutback.demo import require_demo_git

    require_demo_git()

    from cutback.core.retrieval import RetrievalCorpus, RetrievalSettings
    from cutback.core.workspace import private_directory
    from cutback.retrieval_changes import RetrievalProposal, inspect_configuration, prepare_patch
    from cutback.sample_app.retrieval_fixture import fixture
    from cutback.server.worker import run_one

    base = f"/v1/projects/{project_id}"

    def post(path, body):
        response = control.post(path, json=body)
        if response.is_error:
            raise ValueError("SYNTHETIC_RETRIEVAL_CONTROL_FAILED_" + str(response.status_code))
        return response.json()

    workspace.mkdir(parents=True, exist_ok=True)
    root = private_directory(workspace, "retrieval-" + uuid4().hex)
    shutil.copyfile(Path(__file__).parent / "sample_app/retrieval.py", root / "app.py")
    settings = RetrievalSettings(chunk_chars=512, overlap_chars=0, top_k=1)
    corpus_value, cases = fixture()
    corpus = RetrievalCorpus.model_validate(corpus_value)
    (root / "retrieval.json").write_text(
        settings.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    (root / "corpus.json").write_text(corpus.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    (root / "check_application.py").write_text(
        "from app import build_input\n\n\ndef main():\n"
        '    assert "deadline-days=14" in build_input("refund return deadline for amber")\n\n\n'
        'if __name__ == "__main__":\n    main()\n',
        encoding="utf-8",
    )

    def git(*args):
        subprocess.run(
            ["git", "-c", "core.hooksPath=", "-C", str(root), *args],
            check=True,
            capture_output=True,
            timeout=10,
        )

    git("init")
    git("config", "core.autocrlf", "false")
    git("add", ".")
    git(
        "-c",
        "user.name=Cutback synthetic sample",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "Independent synthetic corpus application",
    )
    _, binding = inspect_configuration(root, "retrieval.json")
    workflow = "synthetic-retrieval-search"
    policy = post(
        base + "/capture-policies",
        {
            "enabled": True,
            "approval_ref": "owned-synthetic-retrieval-only",
            "rights_basis": "synthetic",
            "environments": ["staging"],
            "workflows": [workflow],
            "allowed_providers": ["openai"],
            "sample_rate": 1,
            "max_samples": 200,
            "retention_days": 1,
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    dataset = post(
        base + "/datasets",
        {
            "workflow": workflow,
            "policy_id": policy["id"],
            "source_type": "synthetic_fixture",
            "independent_labels": True,
            "representative": False,
            "provenance_ref": "owned-corpus-answer-labels-v1",
            "cases": cases,
        },
    )
    result = {
        "dataset_id": dataset["id"],
        "configuration_binding": binding.model_dump(mode="json"),
        "source_directory": str(root),
        "paid_provider_calls": 0,
    }
    if not execute:
        return result
    validation = post(
        base + "/retrieval/draft",
        {
            "dataset_id": dataset["id"],
            "corpus": corpus.model_dump(mode="json"),
            "settings": settings.model_dump(mode="json"),
            "configuration_binding": binding.model_dump(mode="json"),
            "baseline": {
                "key": "baseline",
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "max_input_tokens": 32768,
                "max_output_tokens": 128,
            },
            "metric": {
                "kind": "exact_label",
                "minimum": 0.9,
                "min_test_samples": 3,
                "min_test_groups": 3,
            },
            "budget": {"max_usd": "1", "max_requests": 200},
            "assumed_usd_per_cpu_hour": "0.01",
            "retain_case_outputs": True,
        },
    )
    executor, calls = synthetic_executor()

    def execute_plan(plan):
        post(
            base + f"/experiments/plans/{plan['id']}/approve",
            {"plan_hash": plan["plan_hash"], "approval_ref": "synthetic-retrieval-only"},
        )
        queued = post(base + "/experiments", {"plan_id": plan["id"]})
        completed = run_one(
            app.state.sessions,
            app.state.artifact_store,
            executor=executor,
            execution_enabled=True,
            only_run_id=UUID(queued["id"]),
        )
        if completed["status"] != "COMPLETED":
            raise ValueError("SYNTHETIC_RETRIEVAL_RUN_FAILED")
        return queued["id"]

    try:
        validation_id = execute_plan(validation)
        confirmation = post(
            base + "/retrieval/confirmation",
            {"validation_run_id": validation_id, "budget": {"max_usd": "1", "max_requests": 100}},
        )
        confirmation_id = execute_plan(confirmation)
        proposal = post(
            base + f"/retrieval/{confirmation_id}/proposal", {"purpose": "synthetic-source-review"}
        )
        change = prepare_patch(root, RetrievalProposal.model_validate(proposal))
        result.update(
            validation_run_id=validation_id,
            confirmation_run_id=confirmation_id,
            native_sdk_calls=len(calls),
            source_change=change,
        )
        return result
    finally:
        executor.close()
