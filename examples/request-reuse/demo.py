"""Independent actual-SDK profile journey. All provider traffic is synthetic."""

import argparse
import base64
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from openai import OpenAI

import cutback
from cutback.instrumentation import workflow


def require_profile_capture(observer, expected, *, timeout_seconds=30):
    """Wait for queued local exports; never resend dropped or uncertain calls."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        drained = observer.flush(min(5, max(0, deadline - time.monotonic())))
        health = observer.health()
        keys = (
            "native_profile_queued",
            "native_profile_exported",
            "native_profile_dropped",
            "captured_dropped",
            "export_failed",
            "rejected",
            "dropped",
        )
        counts = {key: value if type(value := health.get(key)) is int else 0 for key in keys}
        losses = any(counts[key] for key in keys[2:])
        if drained and counts["native_profile_exported"] == expected and not losses:
            return
        if drained or time.monotonic() >= deadline or losses:
            # Only allowlisted numeric diagnostics: no policy, key, input or response.
            detail = " ".join(f"{key}={value}" for key, value in counts.items())
            raise ValueError("OWNED_PROFILE_CAPTURE_INCOMPLETE " + detail)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080")
    parser.add_argument("--keep-project", action="store_true")
    parser.add_argument("--project-slug", help="Unique name for the new synthetic project")
    parser.add_argument(
        "--keep-source",
        type=Path,
        help="Retain the independent application in a new private directory",
    )
    parser.add_argument(
        "--prepare-trial",
        action="store_true",
        help="Prepare a pending workload-bound trial without inference; implies --keep-project",
    )
    parser.add_argument(
        "--source-workload",
        action="store_true",
        help="Also bind a committed independent application and assess its workload",
    )
    args = parser.parse_args()
    if args.prepare_trial:
        if not args.source_workload or args.keep_source is None:
            parser.error(
                "--prepare-trial requires --source-workload and --keep-source NEW_PRIVATE_DIRECTORY"
            )
        args.keep_project = True
    if args.keep_source is not None and not args.source_workload:
        parser.error("--keep-source requires --source-workload")
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
    source_app = None
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
            if args.source_workload:
                from source_application import SourceApplication

                source_app = SourceApplication(root=args.keep_source, with_cases=args.prepare_trial)
            workflow_id = source_app.workflow if source_app else "classify"
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
                    "workflows": [workflow_id],
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
                **({"source_map": str(source_app.source_map)} if source_app else {}),
            )
            with (
                OpenAI(
                    api_key="owned-synthetic-provider-key",
                    max_retries=0,
                    http_client=httpx.Client(
                        transport=httpx.MockTransport(provider), trust_env=False
                    ),
                ) as client,
                workflow(workflow_id),
            ):
                for index in range(8):
                    text = "Owned synthetic billing task " + ("repeat" if index < 4 else str(index))
                    result = (
                        source_app.call(client, text)
                        if source_app
                        else client.responses.create(
                            model="gpt-4.1",
                            input=text,
                            store=False,
                            max_output_tokens=64,
                        )
                    )
                    assert result.output_text == "billing"
            require_profile_capture(observer, 8)
            body = {
                "request_id": str(uuid4()),
                "policy_id": policy["id"],
                "environment": "staging",
                "workflow": workflow_id,
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
            assessment = None
            trial = None
            if source_app:
                from cutback.agent_cache_workload import assess
                from cutback.agent_control import Control

                assessment = assess(
                    source_app.root,
                    Control(endpoint, token, client=control),
                    project,
                    cache_execution={
                        "freshness_ref": "owned-immutable-demo",
                        "ttl_seconds": 300,
                        "content_retention_authorized": True,
                        "immutable_or_safely_invalidated": True,
                        "operation_cost_upper_usd": "0.000000000001",
                        "operation_cost_basis_ref": "owned-fixture-bound",
                    },
                )
                assert assessment["decision"] == "prepare_separate_cache_trial"
                assert source_app.unchanged()
                if args.prepare_trial:
                    from cutback.agent import prepare
                    from cutback.agent_control import connect

                    # Save destination metadata only; the existing administrator
                    # credential remains in this process environment.
                    connect(source_app.root, endpoint=endpoint, project=project)
                    planned = prepare(
                        source_app.root,
                        Control(endpoint, token, client=control),
                        project,
                        files=["cases.jsonl"],
                        rights_basis="synthetic",
                        rights_ref="owned-workload-trial",
                        max_usd="5",
                        max_requests=500,
                        independent_labels=True,
                        representative=False,
                        minimum=1,
                        min_test_samples=3,
                        automatic=True,
                        exact_cache_contract={
                            "freshness_ref": "owned-immutable-demo",
                            "ttl_seconds": 300,
                            "content_retention_authorized": True,
                            "immutable_or_safely_invalidated": True,
                            "operation_cost_upper_usd": "0.000000000001",
                            "operation_cost_basis_ref": "owned-fixture-bound",
                        },
                    )
                    assert planned["phase"] == "PLAN_READY"
                    assert request("GET", f"/v1/projects/{project}/experiments") == []
                    trial = {
                        "plan": planned["plan"],
                        "phase": planned["phase"],
                        "candidate_preparation": planned["workload_candidate_preparation"],
                        "provider_calls": 0,
                        "approval_required": True,
                        "application_source_changed": False,
                    }
            print(
                json.dumps(
                    {
                        "project_id": project,
                        "synthetic_native_calls": len(calls),
                        "paid_provider_calls": 0,
                        "analysis": receipt,
                        "project_retained": args.keep_project,
                        **(
                            {"pending_workload_trial": trial, "source_checkout_retained": True}
                            if trial
                            else {}
                        ),
                        **(
                            {"source_workload": assessment, "application_source_changed": False}
                            if source_app
                            else {}
                        ),
                    },
                    default=str,
                )
            )
        finally:
            if observer:
                observer.close()
            if source_app:
                source_app.close()
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
