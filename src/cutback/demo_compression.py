"""Owned context application -> actual native SDK -> PG -> confirmation -> exact diff."""

import json
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4


def synthetic_executor(*, joint=False):
    import httpx

    from cutback.evals.executors import OpenAIExecutor

    calls = []

    def handle(request):
        if str(request.url) != "https://api.openai.com/v1/responses":
            raise ValueError("SYNTHETIC_COMPRESSION_DESTINATION_REFUSED")
        body = json.loads(request.content)
        if body.get("store") is not False:
            raise ValueError("SYNTHETIC_COMPRESSION_STORAGE_POLICY_REFUSED")
        text = body["input"]
        frame = json.loads(text[text.index("{") :])
        calls.append(len(text.encode()))
        fact = re.search(r"\bdeadline-days=(\d+)\b", frame["context"])
        answer = fact.group(1) if fact else "unknown"
        if joint and body["model"] == "gpt-4.1-mini" and len(frame["context"].splitlines()) > 3:
            answer = "unknown"
        count = len(text.encode())  # Synthetic usage, never a real tokenizer measurement.
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


def seed_compression(app, control, project_id, workspace, *, execute=True, joint=False):
    from cutback.compression_changes import (
        CompressionProposal,
        inspect_configuration,
        prepare_patch,
    )
    from cutback.core.compression import CompressionSettings
    from cutback.core.workspace import private_directory
    from cutback.demo import require_demo_git
    from cutback.sample_app.compression_fixture import fixture
    from cutback.server.worker import run_one

    require_demo_git()
    base = f"/v1/projects/{project_id}"

    def post(path, body):
        response = control.post(path, json=body)
        if response.is_error:
            raise ValueError("SYNTHETIC_COMPRESSION_CONTROL_FAILED_" + str(response.status_code))
        return response.json()

    workspace.mkdir(parents=True, exist_ok=True)
    root = private_directory(workspace, "compression-" + uuid4().hex)
    shutil.copyfile(Path(__file__).parent / "sample_app/compression.py", root / "app.py")
    if joint:
        path = root / "app.py"
        path.write_bytes(path.read_bytes().replace(b'model="gpt-4.1-mini"', b'model="gpt-4.1"'))
    settings = CompressionSettings(max_context_bytes=1024)
    (root / "compression.json").write_text(
        settings.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    cases = fixture()
    (root / "question.json").write_text(cases[0]["input"] + "\n", encoding="utf-8")
    (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    (root / "check_application.py").write_text(
        "import json\nfrom pathlib import Path\nfrom app import build_input\n\n"
        "def main():\n    value=json.loads(Path('question.json').read_text('utf-8'))\n"
        "    text=build_input(value['query'],value['context'])\n"
        "    assert 'deadline-days=14' in text\n\n"
        "if __name__ == '__main__':\n    main()\n",
        encoding="utf-8",
    )

    def git(*args):
        subprocess.run(
            ["git", "-c", "core.hooksPath=", "-c", "core.autocrlf=false", "-C", str(root), *args],
            check=True,
            capture_output=True,
            timeout=10,
        )

    git("init")
    git("add", ".")
    git(
        "-c",
        "user.name=Cutback synthetic sample",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "Independent synthetic context application",
    )
    _, binding = inspect_configuration(root, "compression.json")
    producer = None
    if joint:
        from cutback.joint_context_changes import inspect_producer

        _, _, producer = inspect_producer(root, "app.py")
    route = "model-context" if joint else "compression"
    workflow = "synthetic-joint-model-context" if joint else "synthetic-context-compression"
    policy = post(
        base + "/capture-policies",
        {
            "enabled": True,
            "approval_ref": "owned-synthetic-compression-only",
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
            "provenance_ref": "owned-context-policy-facts-v1",
            "split_policy": "task-aware-v2",
            "cases": cases,
        },
    )
    result = {
        "dataset_id": dataset["id"],
        "configuration_binding": binding.model_dump(mode="json"),
        **({"producer_binding": producer.model_dump(mode="json")} if producer else {}),
        "source_directory": str(root),
        "paid_provider_calls": 0,
    }
    if not execute:
        return result
    plan = post(
        base + f"/{route}/draft",
        {
            "dataset_id": dataset["id"],
            "settings": settings.model_dump(mode="json"),
            "configuration_binding": binding.model_dump(mode="json"),
            **({"producer_binding": producer.model_dump(mode="json")} if producer else {}),
            "baseline": {
                "key": "baseline",
                "provider": "openai",
                "model": "gpt-4.1" if joint else "gpt-4.1-mini",
                "max_input_tokens": 20000,
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
    executor, calls = synthetic_executor(joint=joint)

    def execute_plan(plan):
        post(
            base + f"/experiments/plans/{plan['id']}/approve",
            {"plan_hash": plan["plan_hash"], "approval_ref": "synthetic-compression-only"},
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
            raise ValueError("SYNTHETIC_COMPRESSION_RUN_FAILED")
        return queued["id"]

    try:
        validation = execute_plan(plan)
        confirmation = post(
            base + f"/{route}/confirmation",
            {"validation_run_id": validation, "budget": {"max_usd": "1", "max_requests": 100}},
        )
        confirmation_id = execute_plan(confirmation)
        proposal = post(
            base + f"/{route}/{confirmation_id}/proposal",
            {"purpose": "synthetic-source-review"},
        )
        if joint:
            from cutback.joint_context_changes import (
                JointContextProposal,
            )
            from cutback.joint_context_changes import (
                prepare_patch as joint_patch,
            )

            change = joint_patch(root, JointContextProposal.model_validate(proposal))
        else:
            change = prepare_patch(root, CompressionProposal.model_validate(proposal))
        result.update(
            validation_run_id=validation,
            confirmation_run_id=confirmation_id,
            native_sdk_calls=len(calls),
            source_change=change,
        )
        return result
    finally:
        executor.close()
