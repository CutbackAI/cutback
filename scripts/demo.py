"""Local Compose demo. SYNTHETIC inference fixture; real collector/database HTTP transport."""

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import openai

import cutback
from cutback.instrumentation import workflow
from cutback.scanner import scan


def main():
    root = Path(__file__).resolve().parents[1]
    admin = (root / "infra/compose/secrets/admin_token").read_text().strip()
    endpoint = "http://127.0.0.1:8080"
    (site,) = scan(root / "examples/minimal-openai")["call_sites"]
    with httpx.Client(base_url=endpoint, trust_env=False, timeout=5) as control:
        header = {"Authorization": f"Bearer {admin}"}
        result = control.post(
            "/v1/projects", headers=header, json={"slug": "synthetic-demo-" + uuid4().hex}
        )
        result.raise_for_status()
        project = result.json()["id"]
        credentials = {}
        for role in ("ingest", "viewer"):
            result = control.post(
                f"/v1/projects/{project}/keys", headers=header, json={"role": role}
            )
            result.raise_for_status()
            credentials[role] = result.json()["token"]
    old_token, old_enabled = (
        os.environ.get("CUTBACK_PROJECT_TOKEN"),
        os.environ.get("CUTBACK_ENABLED"),
    )
    os.environ["CUTBACK_PROJECT_TOKEN"] = credentials["ingest"]
    os.environ["CUTBACK_ENABLED"] = "true"
    observer = None
    try:
        observer = cutback.init(
            service_name="synthetic-demo",
            environment="staging",
            enabled=True,
            endpoint=endpoint + "/v1/ingest/events",
            project_id=project,
        )
        fixture = {
            "id": "resp_synthetic_demo",
            "object": "response",
            "created_at": 1,
            "status": "completed",
            "model": "gpt-4.1-mini-2025-04-14",
            "output": [],
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 100,
                "total_tokens": 1100,
                "input_tokens_details": {"cached_tokens": 200},
                "output_tokens_details": {"reasoning_tokens": 20},
            },
        }
        with openai.OpenAI(
            api_key="synthetic-not-a-real-key",
            max_retries=0,
            http_client=httpx.Client(
                transport=httpx.MockTransport(lambda request: httpx.Response(200, json=fixture))
            ),
        ) as client:
            with workflow("workflow:synthetic-demo", call_site_id=site["call_site_id"]):
                response = client.responses.create(
                    model="gpt-4.1-mini", input="Synthetic private fixture"
                )
                assert response.id == fixture["id"]
        if not observer.flush(2) or observer.health().get("exported") != 1:
            raise RuntimeError("synthetic metadata export failed; check collector readiness")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "cutback.cli",
                "report",
                "--source",
                "collector",
                "--endpoint",
                endpoint,
                "--project",
                project,
                "--period",
                "1h",
            ],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "CUTBACK_CONTROL_TOKEN": credentials["viewer"]},
        )
        report = json.loads(result.stdout)
        assert report["summary"]["observed_requests"] == 1
        assert report["summary"]["modeled_cost_usd"] == "0.000500000000"
        artifact = {
            "provenance": "SYNTHETIC_FIXTURE_NOT_LIVE_PROVIDER_OR_CUSTOMER_USAGE",
            "paid_provider_calls": 0,
            "sdk": "openai==2.29.0",
            "report": report,
            "exporter_health": observer.health(),
        }
        destination = root / "artifacts/synthetic-compose-report.json"
        destination.parent.mkdir(exist_ok=True)
        destination.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
        print("Synthetic SDK -> Compose HTTP collector -> PostgreSQL -> CLI: passed.")
        print("No live provider request or paid API call was made.")
        print(f"Labeled report: {destination}")
    finally:
        if observer:
            observer.close()
        for key, previous in (
            ("CUTBACK_PROJECT_TOKEN", old_token),
            ("CUTBACK_ENABLED", old_enabled),
        ):
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous


if __name__ == "__main__":
    main()
