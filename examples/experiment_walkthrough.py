"""SYNTHETIC native SDK -> real collector/PostgreSQL -> authorized paired experiments.

Requires the local Compose PostgreSQL from the quickstart. Uses a temporary real
collector and encrypted store, then deletes only its own synthetic project.
No live provider request, model download, production change or paid API call.
"""

import json
import os
import secrets
import socket
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx
import uvicorn

import cutback
from cutback.evals.artifacts import EncryptedFileStore
from cutback.evals.executors import OpenAIExecutor
from cutback.instrumentation import workflow
from cutback.scanner import scan
from cutback.server.api import create_app
from cutback.server.worker import run_one

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def local_collector(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    instance = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="error"))
    thread = threading.Thread(target=instance.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not instance.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not instance.started:
        raise RuntimeError("Synthetic local collector failed to start")
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        instance.should_exit = True
        thread.join(timeout=5)
        sock.close()


def provider_fixture(request):
    if request.url.host != "api.openai.com" or request.url.path != "/v1/responses":
        raise RuntimeError("The synthetic example refuses all provider network requests")
    body = json.loads(request.content)
    label = "A" if body["input"].endswith("apple") else "B"
    return httpx.Response(
        200,
        json={
            "id": "resp_synthetic",
            "object": "response",
            "created_at": 1,
            "status": "completed",
            "model": body["model"],
            "output": [
                {
                    "id": "msg_synthetic",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": label, "annotations": []}],
                }
            ],
            "usage": {
                "input_tokens": 30,
                "output_tokens": 1,
                "total_tokens": 31,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 0},
            },
        },
    )


def main():
    password = quote((ROOT / "infra/compose/secrets/db_password").read_text().strip(), safe="")
    database_url = os.getenv(
        "CUTBACK_DEMO_DATABASE_URL",
        f"postgresql+psycopg://cutback:{password}@127.0.0.1:55432/cutback",
    )
    if urlsplit(database_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("This synthetic walkthrough only permits a loopback PostgreSQL endpoint")
    saved = {
        name: os.environ.get(name)
        for name in (
            "CUTBACK_ALLOW_PAYLOADS",
            "CUTBACK_EXPERIMENT_EXECUTION",
            "CUTBACK_EXPERIMENT_OPENAI_KEY",
            "CUTBACK_PROJECT_TOKEN",
            "CUTBACK_ENABLED",
        )
    }
    os.environ.update(
        CUTBACK_ALLOW_PAYLOADS="true",
        CUTBACK_EXPERIMENT_EXECUTION="false",
        CUTBACK_EXPERIMENT_OPENAI_KEY="synthetic-not-a-key",
        CUTBACK_ENABLED="true",
    )
    observer = executor = None
    try:
        with tempfile.TemporaryDirectory(prefix="cutback-owned-example-") as directory:
            store = EncryptedFileStore(Path(directory), os.urandom(32))
            admin = secrets.token_urlsafe(48)
            app = create_app(database_url=database_url, admin_token=admin, allow_insecure_http=True)
            app.state.artifact_store = store
            with (
                local_collector(app) as endpoint,
                httpx.Client(
                    base_url=endpoint,
                    trust_env=False,
                    timeout=10,
                    headers={"Authorization": "Bearer " + admin},
                ) as control,
            ):
                control.get("/v1/health/ready").raise_for_status()
                project = control.post(
                    "/v1/projects", json={"slug": "owned-example-" + uuid4().hex}
                ).json()
                base = "/v1/projects/" + project["id"]

                def post(path, body):
                    response = control.post(base + path, json=body)
                    response.raise_for_status()
                    return response.json()

                try:
                    token = post("/keys", {"role": "ingest"})["token"]
                    os.environ["CUTBACK_PROJECT_TOKEN"] = token
                    inventory = scan(Path(cutback.__file__).parent / "evals")
                    sites = [
                        site
                        for site in inventory["call_sites"]
                        if site["source_symbol"] == "OpenAIExecutor.execute"
                        and site["sdk_surface"] == "responses.create"
                    ]
                    if len(sites) != 1:
                        raise RuntimeError(
                            "The actual native executor source must resolve uniquely"
                        )
                    post("/scans", inventory)
                    observer = cutback.init(
                        service_name="owned-product-example",
                        environment="staging",
                        enabled=True,
                        project_id=project["id"],
                        endpoint=endpoint + "/v1/ingest/events",
                        providers=("openai",),
                        capture_content=False,
                    )
                    executor = OpenAIExecutor(
                        http_client=httpx.Client(
                            transport=httpx.MockTransport(provider_fixture), trust_env=False
                        )
                    )
                    policy = post(
                        "/capture-policies",
                        {
                            "enabled": True,
                            "approval_ref": "owned-synthetic-example",
                            "rights_basis": "synthetic",
                            "environments": ["staging"],
                            "workflows": ["synthetic-classify"],
                            "allowed_providers": ["openai"],
                            "sample_rate": 1,
                            "max_samples": 200,
                            "retention_days": 1,
                            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                        },
                    )
                    reports = []
                    for independent in (True, False):
                        cases = []
                        for i in range(80):
                            label = "A" if i % 2 else "B"
                            text = (
                                f"Classify synthetic {chr(65 + i // 26)}{chr(65 + i % 26)} fruit: "
                                + ("apple" if label == "A" else "banana")
                            )
                            cases.append(
                                {
                                    "id": f"owned-{i}",
                                    "input": text,
                                    **({"gold": label} if independent else {"reference": label}),
                                }
                            )
                        dataset = post(
                            "/datasets",
                            {
                                "workflow": "synthetic-classify",
                                "environment": "staging",
                                "policy_id": policy["id"],
                                "source_type": "synthetic_fixture",
                                "provenance_ref": "owned-labels"
                                if independent
                                else "owned-teacher-reference",
                                "independent_labels": independent,
                                "representative": False,
                                "cases": cases,
                            },
                        )
                        spec = {
                            "workflow": "synthetic-classify",
                            "dataset_hash": dataset["immutable_hash"],
                            "baseline": {
                                "key": "baseline",
                                "provider": "openai",
                                "model": "gpt-4.1",
                                "max_output_tokens": 32,
                            },
                            "candidates": [
                                {
                                    "key": "candidate",
                                    "provider": "openai",
                                    "model": "gpt-4.1-mini",
                                    "max_output_tokens": 32,
                                }
                            ],
                            "metric": {
                                "kind": "exact_label" if independent else "reference_match",
                                "minimum": 0.7,
                                "max_regression": 0.3,
                                "min_test_samples": 10,
                                "min_test_groups": 10,
                            },
                            "budget": {"max_usd": "1", "max_requests": 100},
                            "approved_providers": ["openai"],
                        }
                        plan = post(
                            "/experiments/plans",
                            {"dataset_id": dataset["id"], "specification": spec},
                        )
                        post(
                            f"/experiments/plans/{plan['id']}/approve",
                            {
                                "plan_hash": plan["plan_hash"],
                                "approval_ref": "synthetic-no-network-budget",
                            },
                        )
                        queued = post("/experiments", {"plan_id": plan["id"]})
                        with workflow("synthetic-classify", call_site_id=sites[0]["call_site_id"]):
                            outcome = run_one(
                                app.state.sessions, store, executor=executor, execution_enabled=True
                            )
                        if outcome["status"] != "COMPLETED":
                            raise RuntimeError("The synthetic approved experiment did not complete")
                        response = control.get(base + "/experiments/" + queued["id"])
                        response.raise_for_status()
                        evidence = response.json()
                        if any(
                            row["status"] == "VERIFIED_ON_HELDOUT_DATASET"
                            for row in evidence["results"]["recommendations"]
                        ):
                            raise AssertionError(
                                "Synthetic examples cannot verify representative customer quality"
                            )
                        reports.append(
                            {
                                "label_scope": "owned_synthetic_gold"
                                if independent
                                else "teacher_reference_not_independent_gold",
                                "evidence": evidence,
                            }
                        )
                    if not observer.flush(2):
                        raise RuntimeError("Synthetic SDK export did not settle")
                    report = control.get(base + "/usage").json()
                    if report["summary"]["observed_attempts"] != 64:
                        raise AssertionError(
                            "Every synthetic native call must reach the actual database"
                        )
                    result = {
                        "disclosure": "SYNTHETIC FIXTURES; NOT LIVE USAGE OR CUSTOMER QUALITY",
                        "paid_provider_calls": 0,
                        "production_deployed": False,
                        "native_sdk": "openai==2.29.0",
                        "inventory_call_sites": len(inventory["call_sites"]),
                        "usage_report": report,
                        "experiments": reports,
                        "exporter_health": observer.health(),
                    }
                    destination = ROOT / "artifacts/synthetic-experiment-walkthrough.json"
                    destination.parent.mkdir(exist_ok=True)
                    destination.write_text(json.dumps(result, indent=2), encoding="utf-8")
                    print("SYNTHETIC: SDK -> collector -> PostgreSQL -> paired reports: passed.")
                    print(
                        "64 synthetic observations; two comparisons; customer quality NOT VERIFIED."
                    )
                    print("No live provider call, model download, GPU use or production change.")
                    print("Metadata evidence:", destination)
                finally:
                    if observer:
                        observer.close()
                        observer = None
                    control.delete(
                        base, params={"confirm_slug": project["slug"]}
                    ).raise_for_status()
                    print("Deleted only the walkthrough's synthetic project and encrypted objects.")
    finally:
        if executor:
            executor.close()
        if observer:
            observer.close()
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


if __name__ == "__main__":
    main()
