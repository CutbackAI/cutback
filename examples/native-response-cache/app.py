"""Owned PostgreSQL/encrypted-cache/native-SDK fixture; no live model transport."""

import base64
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from openai import OpenAI

from cutback.agent_control import connect
from cutback.core.preparation import PREPARATION_CONTRACT
from cutback.core.prompts import fingerprint
from cutback.core.workspace import private_directory, read_private, workspace_lock, write_private
from cutback.evals.datasets import CapturePolicy
from cutback.local_stack import down, up
from cutback.native_cache import NativeCacheScope, NativeResponsesCache


def context(root, control, project):
    private = private_directory(root, ".cutback/native-cache-fixture")
    with workspace_lock(private):
        path = private / "context.json"
        if path.exists():
            value = read_private(path)
            if (
                value["version"] != "native-cache-fixture-v1"
                or value["project"] != project
                or value["endpoint"] != control.endpoint
                or value["root_fingerprint"] != fingerprint(str(root))
            ):
                raise ValueError("CACHE_FIXTURE_SAVED_IDENTITY_CHANGED")
        else:
            policy = CapturePolicy(
                enabled=True,
                approval_ref="owned-native-cache-fixture",
                rights_basis="synthetic",
                environments=["staging"],
                workflows=["classify"],
                allowed_providers=["openai"],
                expires_at=datetime.now(UTC) + timedelta(days=1),
                retention_days=1,
                sample_rate=1,
                max_samples=20,
            )
            value = {
                "version": "native-cache-fixture-v1",
                "project": project,
                "endpoint": control.endpoint,
                "root_fingerprint": fingerprint(str(root)),
                "policy_id": str(uuid4()),
                "policy": policy.model_dump(mode="json"),
                "request_hmac_key": base64.b64encode(os.urandom(32)).decode(),
                "phase": "PREPARING",
            }
            write_private(path, value, exclusive=True)
        policy = CapturePolicy.model_validate(value["policy"])
        if policy.rights_basis != "synthetic" or policy.expires_at <= datetime.now(UTC):
            raise ValueError("CACHE_FIXTURE_CURRENT_SYNTHETIC_RIGHTS_REQUIRED")
        ready = control("GET", "/v1/health/ready")
        if ready.get("preparation_contract") != PREPARATION_CONTRACT:
            raise ValueError("CACHE_FIXTURE_RESUMABLE_POLICY_CONTRACT_REQUIRED")
        created = control(
            "POST",
            f"/v1/projects/{project}/capture-policies?request_id={value['policy_id']}",
            policy.model_dump(mode="json"),
        )
        if created["id"] != value["policy_id"]:
            raise ValueError("CACHE_FIXTURE_POLICY_IDENTITY_MISMATCH")
        value["phase"] = "READY"
        write_private(path, value)
        connection = read_private(root / ".cutback/agent/connection.json")
        return NativeCacheScope(
            endpoint=control.endpoint,
            project_id=project,
            policy_id=value["policy_id"],
            token=connection["ingest_token"],
            request_hmac_key=base64.b64decode(value["request_hmac_key"], validate=True),
            workflow="classify",
            environment="staging",
            prompt_revision="owned-prompt-v1",
            freshness_ref="owned-synthetic-catalog-v1",
            ttl_seconds=60,
            content_retention_authorized=True,
            immutable_or_safely_invalidated=True,
        )


def run():
    root = Path(__file__).resolve().parent
    old_cache = os.environ.get("CUTBACK_CACHE_ENABLED")
    os.environ["CUTBACK_CACHE_ENABLED"] = "true"
    try:
        up(root, port=8097, allow_payloads=True)
    finally:
        if old_cache is None:
            os.environ.pop("CUTBACK_CACHE_ENABLED", None)
        else:
            os.environ["CUTBACK_CACHE_ENABLED"] = old_cache
    control, project = connect(root, local=True)
    scope = context(root, control, project)
    native_calls = 0

    def closed_provider(request):
        nonlocal native_calls
        native_calls += 1
        body = json.loads(request.content)
        label = "billing" if "invoice" in body["input"] else "incident"
        return httpx.Response(
            200,
            json={
                "id": "resp_owned_cache",
                "object": "response",
                "created_at": 1,
                "model": body["model"],
                "status": "completed",
                "parallel_tool_calls": False,
                "tool_choice": "none",
                "tools": [],
                "output": [
                    {
                        "id": "msg_owned_cache",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": label, "annotations": []}],
                    }
                ],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 2,
                    "total_tokens": 102,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    native = OpenAI(
        api_key="owned-synthetic-only",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(closed_provider), trust_env=False),
    )
    native.organization = None
    native.project = None
    cache = NativeResponsesCache(scope)
    receipts = []
    try:
        for text, expected in [
            ("Owned synthetic invoice question", "billing"),
            ("Owned synthetic outage question", "incident"),
            ("Owned synthetic invoice question", "billing"),
            ("Owned synthetic outage question", "incident"),
        ]:
            result = cache.create(
                native,
                model="gpt-4.1",
                input=text,
                instructions="Return billing or incident",
                max_output_tokens=64,
                store=False,
            )
            assert result.response.output_text == expected, "CACHE_FIXTURE_LABEL_MISMATCH"
            receipts.append(result.receipt)
        hits = sum(item.origin == "encrypted_cache" for item in receipts)
        assert hits >= 2 and native_calls <= 2
        print(
            json.dumps(
                {
                    "status": "passed",
                    "requests": 4,
                    "native_fixture_requests": native_calls,
                    "cache_hits": hits,
                    "paid_provider_calls": 0,
                    "original_native_usage_preserved": True,
                    "cached_usage_origin": "historical_cached_response",
                    "invoice_verified_cost_usd": None,
                    "whole_task_cost_usd": None,
                    "real_model_quality_verified": False,
                    "store": "owned_postgresql_and_encrypted_artifacts",
                    "production_changed": False,
                }
            )
        )
    finally:
        cache.close()
        native.close()


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["down"]:
            print(json.dumps(down(Path(__file__).resolve().parent)))
        elif not sys.argv[1:]:
            run()
        else:
            raise ValueError("CACHE_FIXTURE_UNKNOWN_COMMAND")
    except Exception:  # noqa: BLE001 -- never print credential-bearing API/database/provider errors.
        sys.exit("NATIVE_CACHE_FIXTURE_STOPPED: inspect private state; no automatic native retry.")
