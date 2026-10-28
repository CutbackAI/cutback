"""Independent synthetic metadata incident, through ordinary scoped APIs.

Use an explicitly chosen project. The separately enabled worker processes these
rules; this script makes no inference request and never configures notifications.
"""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx

from cutback.core.config import validate_endpoint


def demonstrate(endpoint, project, token, ingest_token, *, transport=None):
    endpoint = validate_endpoint(endpoint)
    workflow = "synthetic-monitor-" + uuid4().hex[:12]
    now = datetime.now(UTC)
    # Put this sample inside the most recently closed minute, not future traffic.
    anchor = now.replace(second=0, microsecond=0) - timedelta(minutes=1)
    base = f"/v1/projects/{project}"
    with httpx.Client(
        base_url=endpoint, transport=transport, trust_env=False, follow_redirects=False, timeout=10
    ) as client:

        def post(path, value, credential):
            response = client.post(
                path, json=value, headers={"Authorization": "Bearer " + credential}
            )
            if response.status_code not in {200, 201, 202}:
                raise ValueError("SYNTHETIC_MONITOR_STEP_FAILED")
            return response.json()

        rule = post(
            base + "/online-rules",
            {
                "version_id": str(uuid4()),
                "key": workflow,
                "expected_revision": 0,
                "environment": "staging",
                "filter": {"workflow": workflow},
                "evaluator": {"kind": "execution_ok"},
                "starts_at": (anchor - timedelta(minutes=1)).isoformat(),
                "expires_at": (now + timedelta(hours=1)).isoformat(),
                "max_evaluations": 10,
                "approval_ref": "owned-synthetic-monitor-only",
            },
            token,
        )
        alert = post(
            base + "/alert-rules",
            {
                "version_id": str(uuid4()),
                "key": workflow,
                "expected_revision": 0,
                "online_rule_id": rule["id"],
                "window_seconds": 60,
                "grace_seconds": 0,
                "min_samples": 1,
                "threshold": "0.5",
                "cooldown_seconds": 60,
            },
            token,
        )
        started = int((anchor + timedelta(seconds=1)).timestamp()) * 1000000000
        spans = post(
            "/v1/ingest/spans",
            {
                "spans": [
                    {
                        "trace_id": uuid4().hex,
                        "span_id": uuid4().hex[:16],
                        "operation": "synthetic-reply",
                        "workflow": workflow,
                        "service": "synthetic-desk",
                        "release": "synthetic-v1",
                        "kind": "chain",
                        "status": "error",
                        "started_ns": started,
                        "ended_ns": started + 1000000,
                    }
                ]
            },
            ingest_token,
        )
        return {
            "rule_id": rule["id"],
            "alert_id": alert["id"],
            "accepted": spans["accepted"],
            "expected_incident": "after_enabled_worker_evaluates_closed_window",
            "evidence": "synthetic_execution_failure_not_customer_quality",
            "provider_calls": 0,
        }


if __name__ == "__main__":
    import json

    print(
        json.dumps(
            demonstrate(
                os.environ["CUTBACK_ENDPOINT"],
                os.environ["CUTBACK_PROJECT_ID"],
                os.environ["CUTBACK_CONTROL_KEY"],
                os.environ["CUTBACK_INGEST_KEY"],
            ),
            indent=2,
        )
    )
