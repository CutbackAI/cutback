"""Actual cached-current source metadata and encrypted v2 preparation for image recovery."""

SEED = r"""import json, os, runpy, tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4
import httpx
from openai import OpenAI
from openai.types.responses import Response
import cutback
from cutback.agent_control import Control
from cutback.agent_policy_program import prepare
from cutback.application_checks import git
from cutback.core.request_profiles import fingerprint
from cutback.core.workspace import write_private
from cutback.demo_campaign_outcomes import frozen_tasks
from cutback.instrumentation.attribution import build_source_map
from cutback.scanner import scan
from cutback.sample_app.policy_fixture import fixture
from cutback.source_policy import load_policy

assert os.getuid() == 10001
endpoint = "http://127.0.0.1:8080"
control = Control(endpoint, os.environ["CUTBACK_ADMIN_TOKEN"])
project = control("POST", "/v1/projects", {"slug": "owned-cached-current-" + uuid4().hex})["id"]
base = "/v1/projects/" + project
key = control("POST", base + "/keys", {"role": "ingest"})["token"]
writer = Control(endpoint, key)
with tempfile.TemporaryDirectory(prefix="owned-cached-current-") as directory:
    root = Path(directory)
    for name, content in fixture(cached_current=True).items():
        (root / name).write_text(content, encoding="utf-8", newline="")
    git(root, "init", "--template=")
    git(root, "add", ".")
    git(root, "-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
        "commit", "-m", "Independent cached-current image fixture")
    policy = load_policy(root / "policy.json")
    authority = control("POST", base + "/capture-policies", {
        "enabled": True, "approval_ref": "owned-cached-current-synthetic-only",
        "rights_basis": "synthetic", "environments": ["staging"],
        "workflows": [policy.workflow], "allowed_providers": ["openai"],
        "sample_rate": 1, "max_samples": 4000, "max_payload_bytes": 512000,
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    })
    inventory = scan(root)
    private = root / ".cutback"
    private.mkdir()
    mapping = private / "source-map.json"
    mapping.write_text(json.dumps(build_source_map(root, inventory)), encoding="utf-8")
    scope_file = private / "runtime.json"
    cache = policy.cache_execution
    write_private(scope_file, {
        "version": "cutback.native-cache-runtime.v1", "contract": cache.model_dump(mode="json"),
        "scope": {"endpoint": endpoint, "project_id": project, "policy_id": authority["id"],
            "token": key, "request_hmac_key": "owned-image-private-key-" + uuid4().hex,
            "workflow": policy.workflow, "environment": "staging",
            "prompt_revision": policy.policy_sha256, "freshness_ref": cache.freshness_ref,
            "ttl_seconds": cache.ttl_seconds, "content_retention_authorized": True,
            "immutable_or_safely_invalidated": True},
    })
    os.environ.update(CUTBACK_PROJECT_TOKEN=key, CUTBACK_NATIVE_CACHE_SCOPE_FILE=str(scope_file))
    calls = []
    def respond(request):
        assert str(request.url) == "https://api.openai.com/v1/responses"
        body = json.loads(request.content)
        calls.append(1)
        label = "Urgent" if "Priority: urgent." in body["input"] else "Normal"
        return httpx.Response(200, json={
            "id": "resp_owned_cached_current", "object": "response", "created_at": 1,
            "status": "completed", "model": body["model"], "parallel_tool_calls": False,
            "tool_choice": "none", "tools": [], "error": None, "incomplete_details": None,
            "output": [{"id": "msg_owned", "type": "message", "role": "assistant",
                "status": "completed", "content": [{"type": "output_text", "text": label,
                    "annotations": []}]}],
            "usage": {"input_tokens": 24, "output_tokens": 2, "total_tokens": 26,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 0}},
        })
    namespace = runpy.run_path(str(root / "app.py"))
    observer = cutback.init(service_name="owned-current-cache", environment="staging", enabled=True,
        endpoint=endpoint + "/v1/ingest/events", project_id=project, source_map=str(mapping),
        observe_policy_runtime=True, timeout_seconds=2)
    cases = [json.loads(line) for line in (root / "tickets.jsonl").read_text("utf-8").splitlines()]
    try:
        with OpenAI(api_key="owned-synthetic-no-provider-key", max_retries=0,
            http_client=httpx.Client(
                transport=httpx.MockTransport(respond), trust_env=False)) as client:
            for case in cases[:4]:
                answer = namespace["classify"](client, case["input"])
                assert type(answer) is Response and answer.output_text == case["gold"]
        assert len(calls) == 2 and observer.flush(5)
    finally:
        observer.close(5)
    dataset = control("POST", base + "/datasets", {
        "workflow": policy.workflow, "policy_id": authority["id"],
        "source_type": "synthetic_fixture",
        "provenance_ref": "owned-cached-current-image-cases", "independent_labels": True,
        "split_policy": "task-aware-v2", "cases": cases,
    })
    snapshot = frozen_tasks(control, project, writer, policy.workflow,
        "owned-cached-current-baseline", ["success"], "1")
    row = prepare(root, control, project, dataset_id=dataset["id"],
        baseline_snapshot_id=snapshot["id"], producer_file="app.py",
        purpose="owned-cached-current-prepare", max_usd="100", max_requests=2000,
        max_wall_seconds=600, savings_per_success="0.01", forecast_successful_tasks=100000,
        minimum=1, min_test_samples=3, min_test_groups=3, generate=False,
        fixture_transport=True, limit=1)
    assert row["status"] == "READY_FOR_APPROVAL" and row["requests"] == 0
    assert row["economic_envelope"]["reference_runtime_cache_requested"] is True
    detail = control("GET", base + "/source-policy-programs/" + row["id"])
    private_spec = control("POST", base + "/source-policy-programs/" + row["id"] + "/content",
        {"purpose": "owned-cached-current-content-verification"})
    runtime = private_spec["specification"]["runtime_reference"]
    assert runtime["summary"]["states"] == {"hit": 2, "native_miss": 2}
    assert runtime["summary"]["retained_operations"] == 4
    assert len(calls) == 2 and detail["requests"] == 0
    print(json.dumps({"project_id": project, "program_id": row["id"],
        "program_hash": row["program_hash"],
        "public_specification_sha256": fingerprint(detail["specification"]),
        "runtime_summary": runtime["summary"], "actual_native_sdk_invocations": len(calls),
        "prepared_without_inference": True, "paid_provider_calls": 0, "synthetic_only": True}))
"""
