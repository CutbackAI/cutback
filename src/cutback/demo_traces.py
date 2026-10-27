"""Owned synthetic multi-step executions for the installed local demo. No inference."""

import time
from datetime import UTC, datetime
from uuid import uuid4


def seed_execution_sample(client, project, writer, policy_id, workflow):
    headers = {"Authorization": "Bearer " + writer}
    traces = []
    for failed in (True, False):
        trace, session = uuid4().hex, "synthetic-session-" + uuid4().hex[:10]
        start = time.time_ns() - 2_000_000_000
        records = []
        for i, (kind, operation) in enumerate(
            [
                ("agent", "support.answer"),
                ("retriever", "retrieve.policy"),
                ("tool", "lookup.order"),
                ("llm", "compose.answer"),
            ]
        ):
            records.append(
                {
                    "trace_id": trace,
                    "span_id": f"{i + 1:016x}",
                    "parent_span_id": "0000000000000001" if i else None,
                    "session_id": session,
                    "operation": operation,
                    "kind": kind,
                    "service": "synthetic-support",
                    "workflow": workflow,
                    "release": "synthetic-failing" if failed else "synthetic-reference",
                    "started_ns": start + i * 100_000_000,
                    "ended_ns": start + (i + 1) * 100_000_000 if i else start + 500_000_000,
                    "status": "error" if failed and kind == "tool" else "ok",
                }
            )
        for path, data in [
            (
                "/v1/ingest/evaluations",
                {
                    "results": [
                        {
                            "result_id": str(uuid4()),
                            "source": "synthetic-demo",
                            "evaluator": "order-state.correct",
                            "evaluator_version": "1",
                            "trace_id": trace,
                            "score_type": "boolean",
                            "score": not failed,
                            "outcome": "failed" if failed else "passed",
                            "observed_at": datetime.now(UTC).isoformat(),
                        }
                    ]
                },
            ),
            ("/v1/ingest/spans", {"spans": records}),
            (
                "/v1/ingest/trace-content",
                {
                    "policy_id": policy_id,
                    "sample_id": str(uuid4()),
                    "trace_id": trace,
                    "span_id": "0000000000000004",
                    "provider": "openai",
                    "input": "Synthetic order status request.",
                    "output": "Synthetic unavailable order state."
                    if failed
                    else "Synthetic delivered order state.",
                    "provenance_ref": "owned-synthetic-trace-fixture",
                },
            ),
        ]:
            response = client.post(path, json=data, headers=headers)
            if response.status_code not in {200, 201}:
                raise ValueError("SYNTHETIC_TRACE_SEED_FAILED")
        traces.append({"trace_id": trace, "session_id": session, "synthetic_failed": failed})
    return traces
