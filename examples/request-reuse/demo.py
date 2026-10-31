"""Independent actual-SDK profile journey. All provider traffic is synthetic."""

import argparse
import base64
import json
import os
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from openai import OpenAI

import cutback
from cutback.instrumentation import workflow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080")
    parser.add_argument("--keep-project", action="store_true")
    parser.add_argument("--project-slug", help="Unique name for the new synthetic project")
    args = parser.parse_args()
    endpoint = args.endpoint.rstrip("/")
    url = urlsplit(endpoint)
    if (
        url.scheme != "http"
        or url.hostname not in {"127.0.0.1", "localhost", "::1"}
        or (url.username or url.password or url.query or url.fragment or url.path)
    ):
        raise SystemExit("Use an owned loopback HTTP collector for this synthetic example.")
    token = os.getenv("CUTBACK_CONTROL_TOKEN", "")
    if not token:
        raise SystemExit(
            "Set CUTBACK_CONTROL_TOKEN in this process using your local administrator key."
        )
    project = None
    observer = None
    previous = {
        name: os.environ.get(name)
        for name in (
            "CUTBACK_PROJECT_TOKEN",
            "CUTBACK_NATIVE_REQUEST_PROFILE_HMAC_KEY",
            "CUTBACK_ENABLED",
        )
    }
    calls = []

    def provider(request):
        # The native SDK has no transport capable of reaching a provider.
        if request.url.host != "api.openai.com" or request.url.path != "/v1/responses":
            raise ValueError("SYNTHETIC_NATIVE_DESTINATION_CONFLICT")
        calls.append(1)
        return httpx.Response(
            200,
            json={
                "id": "resp_owned_" + str(len(calls)),
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": "gpt-4.1",
                "parallel_tool_calls": False,
                "tool_choice": "none",
                "tools": [],
                "output": [
                    {
                        "id": "msg_owned_" + str(len(calls)),
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": "billing", "annotations": []},
                        ],
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

    with httpx.Client(
        base_url=endpoint,
        headers={"Authorization": "Bearer " + token},
        trust_env=False,
        follow_redirects=False,
        timeout=10,
    ) as control:

        def request(method, path, **kwargs):
            response = control.request(method, path, **kwargs)
            if response.status_code not in {200, 201}:
                # Server errors and arbitrary bodies can contain private deployment details.
                raise ValueError("OWNED_COLLECTOR_REQUEST_FAILED_RECONCILE_BEFORE_RETRY")
            return response.json()

        try:
            project = request(
                "POST",
                "/v1/projects",
                json={
                    "slug": args.project_slug or "request-reuse-" + uuid4().hex,
                    "environment": "staging",
                },
            )["id"]
            writer = request("POST", f"/v1/projects/{project}/keys", json={"role": "ingest"})
            policy = request(
                "POST",
                f"/v1/projects/{project}/capture-policies",
                json={
                    "enabled": True,
                    "approval_ref": "synthetic-request-reuse-demo",
                    "rights_basis": "synthetic",
                    "environments": ["staging"],
                    "workflows": ["classify"],
                    "allowed_providers": ["openai"],
                    "sample_rate": 1,
                    "max_samples": 16,
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    "native_request_profile": {
                        "freshness_ref": "owned-immutable-demo",
                        "prospective_ttl_seconds": 300,
                        "fingerprint_capture_authorized": True,
                        "immutable_or_safely_invalidated": True,
                    },
                },
            )
            os.environ["CUTBACK_PROJECT_TOKEN"] = writer["token"]
            os.environ["CUTBACK_NATIVE_REQUEST_PROFILE_HMAC_KEY"] = base64.b64encode(
                os.urandom(32)
            ).decode()
            os.environ["CUTBACK_ENABLED"] = "true"
            start = datetime.now(UTC)
            observer = cutback.init(
                service_name="request-reuse-demo",
                environment="staging",
                enabled=True,
                endpoint=endpoint + "/v1/ingest/events",
                project_id=project,
                capture_content=True,
                capture_policy_id=policy["id"],
                timeout_seconds=2,
            )
            with (
                OpenAI(
                    api_key="owned-synthetic-provider-key",
                    max_retries=0,
                    http_client=httpx.Client(
                        transport=httpx.MockTransport(provider), trust_env=False
                    ),
                ) as client,
                workflow("classify"),
            ):
                for index in range(8):
                    result = client.responses.create(
                        model="gpt-4.1",
                        input="Owned synthetic billing task "
                        + ("repeat" if index < 4 else str(index)),
                        store=False,
                        max_output_tokens=64,
                    )
                    assert result.output_text == "billing"
            if not observer.flush(5) or observer.health().get("native_profile_exported") != 8:
                raise ValueError("OWNED_PROFILE_CAPTURE_INCOMPLETE")
            body = {
                "request_id": str(uuid4()),
                "policy_id": policy["id"],
                "environment": "staging",
                "workflow": "classify",
                "purpose": "synthetic-demo",
                "window_start": start.isoformat(),
                "window_end": datetime.now(UTC).isoformat(),
                "max_observations": 8,
                "max_wall_seconds": 30,
            }
            base = f"/v1/projects/{project}/native-request-profiles/analyses"
            receipt = request("POST", base, json=body)
            deadline = time.monotonic() + 60
            while receipt["state"] in {"QUEUED", "RUNNING"} and time.monotonic() < deadline:
                time.sleep(0.2)
                receipt = request("GET", base + "/" + receipt["id"])
            if receipt["state"] != "COMPLETED":
                raise ValueError("OWNED_ANALYSIS_NOT_COMPLETED_CHECK_LOCAL_WORKER")
            summary = receipt["summary"]
            assert len(calls) == 8 and summary["profiled_attempts"] == 8
            assert summary["modeled_potential_hits"] == 3
            assert summary["cache_trial_eligible"] is True
            print(
                json.dumps(
                    {
                        "project_id": project,
                        "synthetic_native_calls": len(calls),
                        "paid_provider_calls": 0,
                        "analysis": receipt,
                        "project_retained": args.keep_project,
                    },
                    default=str,
                )
            )
        finally:
            if observer:
                observer.close()
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            if project and not args.keep_project:
                metadata = request("GET", f"/v1/projects/{project}")
                request(
                    "DELETE", f"/v1/projects/{project}", params={"confirm_slug": metadata["slug"]}
                )


if __name__ == "__main__":
    main()
