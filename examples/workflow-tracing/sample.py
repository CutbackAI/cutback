"""Synthetic multi-step execution -> real collector metadata; no inference API calls."""

import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4


def demonstration():
    body = json.loads(Path(__file__).with_name("openinference-v1.json").read_text(encoding="utf-8"))
    spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
    delta = time.time_ns() - 1_000_000_000 - min(int(s["startTimeUnixNano"]) for s in spans)
    trace = uuid4().hex
    session = "synthetic-" + uuid4().hex[:12]
    for span in spans:
        span["traceId"] = trace
        for key in ("startTimeUnixNano", "endTimeUnixNano"):
            span[key] = str(int(span[key]) + delta)
        for attr in span["attributes"]:
            if attr["key"] == "session.id":
                attr["value"] = {"stringValue": session}
        if any(
            a["key"] == "cutback.operation" and a["value"].get("stringValue") == "lookup.order"
            for a in span["attributes"]
        ):
            span["status"] = {"code": 2, "message": "Synthetic timeout: deliberately discarded"}
    evaluation = {
        "results": [
            {
                "result_id": str(uuid4()),
                "source": "synthetic-support-fixture",
                "evaluator": "order-state.correct",
                "evaluator_version": "1",
                "trace_id": trace,
                "score_type": "boolean",
                "score": False,
                "outcome": "failed",
                "observed_at": datetime.now(UTC).isoformat(),
            }
        ]
    }
    return body, evaluation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print synthetic metadata without making any request"
    )
    args = parser.parse_args()
    body, evaluation = demonstration()
    if args.dry_run:
        print(json.dumps({"traces": body, "evaluations": evaluation}, indent=2))
        return
    import httpx

    parsed = urlsplit(args.endpoint)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or parsed.scheme == "http"
        and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
    ):
        parser.error("Use a collector origin with HTTPS, or loopback HTTP")
    token = os.environ.get("CUTBACK_PROJECT_TOKEN")
    if not token:
        parser.error(
            "Set CUTBACK_PROJECT_TOKEN to a scoped staging ingest key; it is never printed"
        )
    with httpx.Client(
        base_url=args.endpoint.rstrip("/"),
        headers={"Authorization": "Bearer " + token},
        trust_env=False,
        follow_redirects=False,
        timeout=10,
    ) as client:
        # Exercise delayed attachment: a result may arrive before its trace.
        for path, data in (
            ("/v1/ingest/evaluations", evaluation),
            ("/v1/ingest/workflow-otel/v1/traces", body),
        ):
            response = client.post(path, json=data)
            if response.status_code != 200:
                raise SystemExit(
                    f"Collector rejected synthetic evidence (HTTP {response.status_code}); "
                    "check scope and migrations"
                )
    print("Synthetic session ingested. Open Dashboard → Traces → Browse sessions.")
    print(
        "Expected: four spans, lookup.order execution error, failed external task check, "
        "unknown total cost."
    )


if __name__ == "__main__":
    main()
