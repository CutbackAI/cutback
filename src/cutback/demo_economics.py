"""Independent actual-SDK task receipts with owned transports and no inference sockets."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4


def seed_task_economics(client, endpoint, project, writer):
    import httpx
    from openai import OpenAI
    from opentelemetry import trace

    from cutback.instrumentation import workflow
    from cutback.instrumentation.openai import OpenAIInstrumentation

    headers = {"Authorization": "Bearer " + writer}
    service, workflow_id = "synthetic-task-economics", "synthetic.order-check"
    observer = OpenAIInstrumentation(
        service_name=service,
        environment="staging",
        endpoint=endpoint + "/v1/ingest/events",
        project_id=project,
        token=writer,
        identity=("owned-task-economics",),
    )

    def fixture(request):
        if request.url.host != "api.openai.com" or request.url.path != "/v1/responses":
            raise ValueError("SYNTHETIC_PROVIDER_DESTINATION_REFUSED")
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "resp_synthetic_" + uuid4().hex,
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": body["model"],
                "output": [
                    {
                        "type": "message",
                        "id": "msg_synthetic",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {"delivered": body["input"] != "synthetic-wrong-answer"}
                                ),
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 10000,
                    "output_tokens": 1000,
                    "total_tokens": 11000,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    tasks = []
    try:
        for release, succeeds, attempts, other in (
            ("synthetic-reference", True, 2, "0.01"),
            ("synthetic-reference", True, 2, "0.01"),
            ("synthetic-cheap-but-worse", True, 1, "0.01"),
            ("synthetic-cheap-but-worse", False, 1, "0.01"),
            ("synthetic-incomplete", True, 1, None),
        ):
            tid, sid = uuid4().hex, uuid4().hex[:16]
            occurred = datetime.now(UTC)
            epoch = datetime(1970, 1, 1, tzinfo=UTC)
            # Use the same UTC clock/precision as native event timestamps. Mixing
            # time.time_ns with datetime on Windows can introduce apparent skew.
            start = ((occurred - epoch) // timedelta(microseconds=1)) * 1000
            parent = trace.NonRecordingSpan(
                trace.SpanContext(
                    trace_id=int(tid, 16),
                    span_id=int(sid, 16),
                    is_remote=False,
                    trace_flags=trace.TraceFlags(trace.TraceFlags.SAMPLED),
                )
            )
            with OpenAI(
                api_key="synthetic-no-provider-key",
                max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(fixture), trust_env=False),
            ) as provider:
                with trace.use_span(parent), workflow(workflow_id):
                    for _ in range(attempts):
                        response = provider.responses.create(
                            model="gpt-4.1" if release == "synthetic-reference" else "gpt-4.1-mini",
                            input="synthetic-right-answer"
                            if succeeds
                            else "synthetic-wrong-answer",
                            max_output_tokens=1024,
                        )
                        # Explicit declared task check, separate from response/HTTP success.
                        passed = json.loads(response.output_text)["delivered"] is True
            span = {
                "trace_id": tid,
                "span_id": sid,
                "operation": workflow_id,
                "kind": "agent",
                "service": service,
                "workflow": workflow_id,
                "release": release,
                "started_ns": start,
                "ended_ns": ((datetime.now(UTC) - epoch) // timedelta(microseconds=1)) * 1000,
                "status": "ok",
            }
            outcome = {
                "task_id": str(uuid4()),
                "result_id": str(uuid4()),
                "environment": "staging",
                "trace_id": tid,
                "workflow": workflow_id,
                "service": service,
                "release": release,
                "occurred_at": occurred.isoformat(),
                "outcome": "success" if passed else "failure",
                "source": "owned-synthetic-check",
                "source_version": "v1",
                "basis": "deterministic_evaluation",
                "expected_native_attempts": attempts,
                "other_costs_declared_complete": True,
                "components": [{"key": "synthetic-lookup", "kind": "tool", "amount_usd": other}],
            }
            for path, body in (
                ("/v1/ingest/spans", {"spans": [span]}),
                ("/v1/ingest/task-outcomes", {"items": [outcome]}),
            ):
                result = client.post(path, headers=headers, json=body)
                if result.status_code != 200:
                    import re

                    code = result.json().get("code", "REQUEST_FAILED")
                    code = (
                        code
                        if isinstance(code, str) and re.fullmatch(r"[A-Z_]{3,100}", code)
                        else "REQUEST_FAILED"
                    )
                    raise ValueError("SYNTHETIC_TASK_SEED_FAILED_" + code)
            tasks.append(outcome)
        if not observer.flush(5):
            raise ValueError("SYNTHETIC_TASK_OBSERVATION_FLUSH_FAILED")
        return {
            "workflow": workflow_id,
            "tasks": len(tasks),
            "native_calls": 7,
            "paid_provider_calls": 0,
            "disclosure": "synthetic_owned_transport_not_customer_evidence",
        }
    finally:
        observer.close(5)
