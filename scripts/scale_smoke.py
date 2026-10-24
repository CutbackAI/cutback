"""Synthetic local ingestion/idempotency/query smoke, not a production throughput SLA."""

import argparse
import json
import platform
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import httpx

from cutback.core.models import LLMEvent

ROOT = Path(__file__).resolve().parents[1]


def main(events=10000):
    if not 200 <= events <= 50000 or events % 200:
        raise ValueError("Use a multiple of 200 from 200 to 50000")
    # This tool is deliberately bound to the owned local Compose collector.
    token = (ROOT / "infra/compose/secrets/admin_token").read_text().strip()
    headers = {"Authorization": "Bearer " + token}
    with httpx.Client(
        base_url="http://127.0.0.1:8080", headers=headers, timeout=10, trust_env=False
    ) as control:
        control.get("/v1/health/ready").raise_for_status()
        response = control.post("/v1/projects", json={"slug": "synthetic-capacity-" + uuid4().hex})
        response.raise_for_status()
        project = response.json()
        base = "/v1/projects/" + project["id"]
        try:
            ingest = control.post(base + "/keys", json={"role": "ingest"}).json()["token"]
            accepted, duplicate = 0, 0
            started = time.perf_counter()
            for offset in range(0, events, 200):
                records = []
                for index in range(offset, offset + 200):
                    model = "gpt-4.1-mini" if index % 2 else "unknown-synthetic-model"
                    records.append(
                        LLMEvent(
                            environment="staging",
                            service_name="synthetic-capacity",
                            provider="openai",
                            sdk_surface="responses.create",
                            requested_model=model,
                            response_model=model,
                            trace_id=uuid4().hex,
                            span_id=uuid4().hex[:16],
                            logical_request_id=uuid5(UUID(project["id"]), str(index // 2)),
                            duration_ms=index % 100 + 1,
                            status="success",
                            input_tokens=100,
                            output_tokens=20,
                            cache_read_tokens=0,
                            reasoning_tokens=0,
                            usage_source="provider_reported",
                            metering_status="text_only",
                        ).model_dump(mode="json")
                    )
                body = {"project_id": project["id"], "events": records}
                for replay in (False, True):
                    response = control.post(
                        "/v1/ingest/events",
                        headers={"Authorization": "Bearer " + ingest},
                        json=body,
                    )
                    response.raise_for_status()
                    receipt = response.json()
                    if replay:
                        assert receipt["accepted"] == 0 and receipt["duplicate"] == 200
                        duplicate += receipt["duplicate"]
                    else:
                        assert receipt["accepted"] == 200 and receipt["duplicate"] == 0
                        accepted += receipt["accepted"]
            ingestion_seconds = time.perf_counter() - started
            query_started = time.perf_counter()
            response = control.get(base + "/usage")
            response.raise_for_status()
            query_ms = (time.perf_counter() - query_started) * 1000
            summary = response.json()["summary"]
            assert accepted == duplicate == summary["observed_attempts"] == events
            assert (
                summary["modeled_cost_usd"] is None and summary["unpriced_requests"] == events // 2
            )
            assert Decimal(summary["known_modeled_cost_subtotal_usd"]) == Decimal("0.000072") * (
                events // 2
            )
            output = {
                "disclosure": "SYNTHETIC EVENT INGESTION; NOT PROVIDER OR CUSTOMER PERFORMANCE",
                "occurred_at": datetime.now(UTC).isoformat(),
                "events": events,
                "duplicate_replays": duplicate,
                "ingestion_seconds": round(ingestion_seconds, 4),
                "event_receipts_per_second": round(events / ingestion_seconds, 2),
                "usage_query_wall_ms": round(query_ms, 3),
                "host_os": platform.system(),
                "architecture": platform.machine(),
                "python": platform.python_version(),
                "unknown_cost_preserved": True,
                "project_deleted_after_smoke": True,
                "production_capacity_claim": False,
            }
            path = ROOT / "artifacts/synthetic-scale-smoke.json"
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps(output, indent=2), encoding="utf-8")
            print(json.dumps(output))
        finally:
            control.delete(base, params={"confirm_slug": project["slug"]}).raise_for_status()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=int, default=10000)
    main(parser.parse_args().events)
