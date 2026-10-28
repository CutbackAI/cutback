"""Independent captured-output quality sample. No model or notification calls."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx

from cutback.core.config import validate_endpoint


def demonstrate(endpoint, project, token, ingest_token, *, transport=None):
    workflow = "synthetic-quality-" + uuid4().hex[:12]
    now = datetime.now(UTC)
    base = f"/v1/projects/{project}"
    with httpx.Client(
        base_url=validate_endpoint(endpoint),
        transport=transport,
        trust_env=False,
        follow_redirects=False,
        timeout=10,
    ) as client:

        def post(path, body, key):
            response = client.post(path, json=body, headers={"Authorization": "Bearer " + key})
            if response.status_code not in {200, 201, 202}:
                raise ValueError("SYNTHETIC_QUALITY_STEP_FAILED")
            return response.json()

        policy = post(
            base + "/capture-policies",
            {
                "enabled": True,
                "approval_ref": "owned-synthetic-quality-only",
                "rights_basis": "synthetic",
                "environments": ["staging"],
                "workflows": [workflow],
                "allowed_providers": ["local"],
                "max_samples": 10,
                "sample_rate": 1,
                "retention_days": 1,
                "expires_at": (now + timedelta(days=1)).isoformat(),
            },
            token,
        )
        rule = post(
            base + "/quality-rules",
            {
                "version_id": str(uuid4()),
                "key": workflow,
                "expected_revision": 0,
                "environment": "staging",
                "workflow": workflow,
                "source_policy_id": policy["id"],
                "evaluator": "nonempty_output",
                "content_use_authorized": True,
                "record_input_features": True,
                "starts_at": (now - timedelta(minutes=1)).isoformat(),
                "expires_at": (now + timedelta(hours=1)).isoformat(),
                "max_evaluations": 2,
                "max_requests": 0,
                "max_total_usd": "0",
                "approval_ref": "owned-synthetic-quality-only",
            },
            token,
        )
        for number, output in enumerate(("Owned synthetic answer", "")):
            trace, span = uuid4().hex, uuid4().hex[:16]
            start = int(now.timestamp()) * 1000000000 + number * 1000000
            post(
                "/v1/ingest/spans",
                {
                    "spans": [
                        {
                            "trace_id": trace,
                            "span_id": span,
                            "operation": "synthetic-answer",
                            "workflow": workflow,
                            "service": "synthetic-quality-desk",
                            "release": "synthetic-v1",
                            "kind": "chain",
                            "status": "ok",
                            "started_ns": start,
                            "ended_ns": start + 1000000,
                        }
                    ]
                },
                ingest_token,
            )
            post(
                "/v1/ingest/trace-content",
                {
                    "policy_id": policy["id"],
                    "sample_id": str(uuid4()),
                    "trace_id": trace,
                    "span_id": span,
                    "provider": "local",
                    "input": f"Owned synthetic question {number}",
                    "output": output,
                    "provenance_ref": "owned-synthetic-quality-only",
                },
                ingest_token,
            )
        return {
            "rule_id": rule["id"],
            "workflow": workflow,
            "online_rule_id": rule["online_rule_id"],
            "provider_calls": 0,
            "expected_after_enabled_worker": {"passed": 1, "failed": 1},
            "evidence": "synthetic_nonempty_contract_not_task_success",
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
