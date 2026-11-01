"""Closed-transport native/cache observations for the disposable image recovery test.

The source runs inside the image against its real local collector. Credentials
arrive in the existing anonymous stdin channel and never appear in the receipt.
"""

SEED = r"""import json, os, runpy, tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4
import httpx
from openai import OpenAI
from openai.types.responses import Response
import cutback
from cutback.application_checks import git
from cutback.core.exact_cache import ExactCacheExecution
from cutback.core.improvement_policy import SourceImprovementPolicy
from cutback.core.policy_producer import PROGRAM
from cutback.core.workspace import write_private
from cutback.evals.experiments import ModelSpec
from cutback.instrumentation.attribution import build_source_map
from cutback.scanner import scan

assert os.getuid() == 10001
endpoint = "http://127.0.0.1:8080"
with httpx.Client(
    base_url=endpoint,
    headers={"Authorization": "Bearer " + os.environ["CUTBACK_ADMIN_TOKEN"]},
    trust_env=False,
    timeout=10,
) as control:

    def post(path, body):
        result = control.post(path, json=body)
        result.raise_for_status()
        return result.json()

    project = post("/v1/projects", {"slug": "owned-policy-activity-" + uuid4().hex})["id"]
    base = "/v1/projects/" + project
    key = post(base + "/keys", {"role": "ingest"})["token"]
    workflow = "owned-policy-cache-activity"
    authority = post(
        base + "/capture-policies",
        {
            "enabled": True,
            "approval_ref": "owned-image-synthetic-only",
            "rights_basis": "synthetic",
            "environments": ["staging"],
            "workflows": [workflow],
            "allowed_providers": ["openai"],
            "sample_rate": 1,
            "max_samples": 20,
            "max_payload_bytes": 64000,
            "retention_days": 1,
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    contract = ExactCacheExecution(
        freshness_ref="owned-image-immutable",
        ttl_seconds=60,
        content_retention_authorized=True,
        immutable_or_safely_invalidated=True,
        operation_cost_upper_usd="0.000000001",
        operation_cost_basis_ref="owned-image-declared-upper",
    )
    target = ModelSpec(
        key="reference",
        provider="openai",
        model="gpt-4.1",
        instruction="Owned synthetic classification",
        max_output_tokens=64,
    )
    policy = SourceImprovementPolicy(
        workflow=workflow,
        max_input_bytes=256,
        baseline=target,
        strategy=target,
        cache_execution=contract,
    )
    calls = []

    def provider(request):
        assert str(request.url) == "https://api.openai.com/v1/responses"
        calls.append(1)
        return httpx.Response(
            200,
            json={
                "id": "resp_owned_activity",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": "gpt-4.1",
                "parallel_tool_calls": False,
                "tool_choice": "none",
                "tools": [],
                "error": None,
                "incomplete_details": None,
                "output": [
                    {
                        "id": "msg_owned_activity",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "OWNED_PRIVATE_IMAGE_OUTPUT",
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
            },
        )

    with tempfile.TemporaryDirectory(prefix="owned-policy-activity-") as temporary:
        root = Path(temporary)
        (root / "app.py").write_text(PROGRAM, encoding="utf-8")
        (root / "policy.json").write_bytes(policy.configuration_bytes())
        (root / ".gitignore").write_text(".cutback/\n__pycache__/\n", encoding="utf-8")
        git(root, "init", "--template=")
        git(root, "add", ".")
        git(
            root,
            "-c",
            "user.name=Synthetic",
            "-c",
            "user.email=synthetic@example.invalid",
            "commit",
            "-m",
            "Independent policy activity image fixture",
        )
        inventory = scan(root)
        site = next(value for value in inventory["call_sites"] if value["source_file"] == "app.py")
        directory = root / ".cutback"
        directory.mkdir()
        mapping = directory / "source-map.json"
        mapping.write_text(json.dumps(build_source_map(root, inventory)), encoding="utf-8")
        private = directory / "runtime.json"
        write_private(
            private,
            {
                "version": "cutback.native-cache-runtime.v1",
                "contract": contract.model_dump(mode="json"),
                "scope": {
                    "endpoint": endpoint,
                    "project_id": project,
                    "policy_id": authority["id"],
                    "token": key,
                    "request_hmac_key": "owned-image-private-key-" + uuid4().hex,
                    "workflow": workflow,
                    "environment": "staging",
                    "prompt_revision": policy.policy_sha256,
                    "freshness_ref": contract.freshness_ref,
                    "ttl_seconds": 60,
                    "content_retention_authorized": True,
                    "immutable_or_safely_invalidated": True,
                },
            },
        )
        os.environ.update(
            CUTBACK_PROJECT_TOKEN=key,
            CUTBACK_CACHE_ENABLED="true",
            CUTBACK_NATIVE_CACHE_SCOPE_FILE=str(private),
        )
        namespace = runpy.run_path(str(root / "app.py"))
        observer = cutback.init(
            service_name="owned-image-policy",
            environment="staging",
            enabled=True,
            endpoint=endpoint + "/v1/ingest/events",
            project_id=project,
            source_map=str(mapping),
            observe_policy_runtime=True,
            timeout_seconds=2,
        )
        started = datetime.now(UTC) - timedelta(seconds=1)
        try:
            with OpenAI(
                api_key="owned-synthetic-no-provider-key",
                max_retries=1,
                http_client=httpx.Client(transport=httpx.MockTransport(provider), trust_env=False),
            ) as client:
                first = namespace["classify"](client, "OWNED_PRIVATE_IMAGE_INPUT")
                hit = namespace["classify"](client, "OWNED_PRIVATE_IMAGE_INPUT")
                assert (
                    type(first) is type(hit) is Response
                    and first.output_text == hit.output_text
                    and len(calls) == 1
                )
                os.environ.pop("CUTBACK_NATIVE_CACHE_SCOPE_FILE")
                assert (
                    type(namespace["classify"](client, "OWNED_PRIVATE_IMAGE_INPUT")) is Response
                    and len(calls) == 2
                )
                os.environ["CUTBACK_NATIVE_CACHE_SCOPE_FILE"] = str(private)
                assert (
                    control.delete(base + "/capture-policies/" + authority["id"]).status_code == 200
                )
                try:
                    namespace["classify"](client, "OWNED_PRIVATE_IMAGE_INPUT")
                except ValueError as error:
                    assert str(error) == "NATIVE_CACHE_CURRENT_POLICY_AND_SCOPED_KEY_REQUIRED"
                else:
                    raise AssertionError("Current revocation was not enforced")
                assert len(calls) == 2 and client.max_retries == 1 and not client.is_closed()
            assert observer.flush(5)
        finally:
            observer.close(5)
        scope = {
            "environment": "staging",
            "service_name": "owned-image-policy",
            "workflow": workflow,
            "call_site_id": site["call_site_id"],
            "source_commit": inventory["project"]["commit"],
            "policy_sha256": policy.policy_sha256,
            "window_start": started.isoformat(),
            "window_end": (datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
        }
        summary = post(base + "/policy-runtime/inspect", scope)
        assert summary["retained_operations"] == 4 and summary["states"] == {
            "native_miss": 1,
            "hit": 1,
            "inactive": 1,
            "unknown": 1,
        }
        assert summary["native_sdk_invocations"] is summary["cache_component_upper_usd"] is None
        assert (
            summary["known_native_sdk_invocations"] == 2
            and summary["unknown_native_operations"] == 1
        )
        assert summary["known_cache_operations"] == 3 and summary["unknown_cache_operations"] == 1
        assert not summary["content_included"] and not summary["execution_authorized"]
        rendered = json.dumps(summary)
        assert all(
            value not in rendered
            for value in (
                "OWNED_PRIVATE_IMAGE_INPUT",
                "OWNED_PRIVATE_IMAGE_OUTPUT",
                key,
                str(private),
            )
        )
print(
    json.dumps(
        {
            "project_id": project,
            "summary": summary,
            "synthetic_only": True,
            "paid_provider_calls": 0,
            "actual_native_sdk_invocations": len(calls),
            "cache_hit_native_response_preserved": True,
            "current_revocation_verified": True,
        }
    )
)
"""
