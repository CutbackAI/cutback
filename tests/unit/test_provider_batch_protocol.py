"""Native pinned SDK batch protocol with synthetic HTTP; no provider call."""

import json
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from cutback.evals.experiments import ModelSpec
from cutback.evals.provider_batch import (
    BatchResult,
    file_sha256,
    input_file,
    price_reference,
    provider_id,
    reservation_bound,
    result_files,
)
from cutback.evals.provider_batch_sdk import OpenAIBatchAdapter


def model():
    return ModelSpec(key="reference", provider="openai", model="gpt-4.1", max_input_tokens=8192)


def line(identifier, *, text="billing", usage=True):
    body = {
        "id": "resp_owned",
        "object": "response",
        "created_at": 1,
        "model": "gpt-4.1-2025-04-14",
        "status": "completed",
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "output": [
            {
                "id": "msg_owned",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
    }
    if usage:
        body["usage"] = {
            "input_tokens": 1000,
            "output_tokens": 2,
            "total_tokens": 1002,
            "input_tokens_details": {"cached_tokens": 200},
            "output_tokens_details": {"reasoning_tokens": 0},
        }
    return {
        "id": "batch_req_owned",
        "custom_id": identifier,
        "response": {"status_code": 200, "request_id": "req_owned", "body": body},
        "error": None,
    }


def encoded(*rows):
    return b"".join(json.dumps(row).encode() + b"\n" for row in rows)


def test_batch_input_and_output_order_native_usage_and_distinct_price_contract():
    ids = [str(uuid4()), str(uuid4())]
    blob = input_file([(identifier, model(), "Independent synthetic ticket") for identifier in ids])
    assert [json.loads(row)["custom_id"] for row in blob.splitlines()] == ids
    assert all(json.loads(row)["body"]["store"] is False for row in blob.splitlines())
    result = result_files(
        [encoded(line(ids[1], text="access"), line(ids[0]))], dict.fromkeys(ids, model())
    )
    assert result[ids[0]].output == "billing" and result[ids[1]].output == "access"
    assert result[ids[0]].cost_usd == Decimal("0.000858")
    assert result[ids[0]].response_model == "gpt-4.1-2025-04-14"
    assert result[ids[0]].cost_usd < reservation_bound(model())
    assert not hasattr(result[ids[0]], "latency_ms")
    assert price_reference()["discount_multiplier"] == "0.5"


def test_partial_missing_unknown_usage_remain_unknown_not_free():
    ids = [str(uuid4()), str(uuid4())]
    result = result_files([encoded(line(ids[0], usage=False))], dict.fromkeys(ids, model()))
    assert result[ids[0]].output == "billing" and result[ids[0]].cost_usd is None
    assert result[ids[1]] == BatchResult(code="BATCH_RESULT_MISSING")


@pytest.mark.parametrize(
    "kind", ["boolean_tokens", "string_tokens", "missing_cache_reads", "unqualified_cache_writes"]
)
def test_native_usage_never_coerces_counts_or_invents_unsupported_billing(kind):
    identifier = str(uuid4())
    row = line(identifier)
    usage = row["response"]["body"]["usage"]
    if kind == "boolean_tokens":
        usage["input_tokens"] = True
    elif kind == "string_tokens":
        usage["input_tokens"] = "1000"
    elif kind == "missing_cache_reads":
        usage["input_tokens_details"].pop("cached_tokens")
    else:
        usage["input_tokens_details"]["cache_write_tokens"] = 10
    result = result_files([encoded(row)], {identifier: model()})[identifier]
    assert result.cost_usd is None
    assert result.output == "billing"
    assert result.response_model == "gpt-4.1-2025-04-14"


@pytest.mark.parametrize("kind", ["duplicate", "foreign", "duplicate_json", "nan", "big_line"])
def test_ambiguous_malformed_or_unbounded_provider_files_never_settle_prefix(kind):
    identifier = str(uuid4())
    row = line(identifier)
    blob = encoded(row)
    if kind == "duplicate":
        blob += encoded(row)
    if kind == "foreign":
        blob += encoded(line(str(uuid4())))
    if kind == "duplicate_json":
        blob = blob.replace(b'"custom_id":', b'"custom_id":"duplicate", "custom_id":')
    if kind == "nan":
        blob = blob.replace(b'"created_at": 1', b'"created_at": NaN')
    if kind == "big_line":
        blob += b"x" * 256001
    with pytest.raises(ValueError, match="BATCH_"):
        result_files([blob], {identifier: model()})


@pytest.mark.parametrize(
    "value", ["https://outside.invalid/file", "../file", "file-a/content", "batch_abc?secret=x"]
)
def test_remote_identifiers_cannot_supply_urls_or_paths(value):
    with pytest.raises(ValueError, match="BATCH_PROVIDER_IDENTIFIER_INVALID"):
        provider_id(value, "file")


def test_one_file_requires_one_native_model_and_unique_opaque_reservations():
    identifier = str(uuid4())
    target = model()
    with pytest.raises(ValueError, match="OPAQUE"):
        input_file([("customer-secret-case", target, "owned")])
    with pytest.raises(ValueError, match="DUPLICATE"):
        input_file([(identifier, target, "owned"), (identifier, target, "owned")])
    with pytest.raises(ValueError, match="ONE_NATIVE_MODEL"):
        input_file(
            [
                (identifier, target, "owned"),
                (str(uuid4()), target.model_copy(update={"model": "gpt-4.1-mini"}), "owned"),
            ]
        )


def test_native_upload_submit_poll_cancel_download_delete_and_no_payload_logging(
    caplog, monkeypatch
):
    operation, call = str(uuid4()), str(uuid4())
    requests = []
    blob = input_file([(call, model(), "Owned confidential synthetic input")])
    monkeypatch.setenv("OPENAI_BASE_URL", "https://outside.invalid")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "unapproved-project")
    monkeypatch.setenv("OPENAI_ORG_ID", "unapproved-org")
    snapshot = {
        "id": "batch_owned",
        "object": "batch",
        "endpoint": "/v1/responses",
        "input_file_id": "file-owned",
        "completion_window": "24h",
        "created_at": 1,
        "status": "in_progress",
        "metadata": {"cutback_operation_id": operation},
        "request_counts": {"total": 1, "completed": 0, "failed": 0},
    }

    def respond(request):
        requests.append(request)
        assert request.url.host == "api.openai.com"
        assert (
            "OpenAI-Organization" not in request.headers and "OpenAI-Project" not in request.headers
        )
        if request.url.path == "/v1/files" and request.method == "POST":
            assert blob in request.content
            return httpx.Response(
                200,
                json={
                    "id": "file-owned",
                    "object": "file",
                    "bytes": len(blob),
                    "created_at": 1,
                    "filename": "cutback-" + operation + ".jsonl",
                    "purpose": "batch",
                },
            )
        if request.url.path == "/v1/batches" and request.method == "POST":
            payload = json.loads(request.content)
            assert payload["metadata"] == {"cutback_operation_id": operation}
            return httpx.Response(200, json=snapshot)
        if request.url.path == "/v1/batches":
            return httpx.Response(
                200, json={"object": "list", "data": [snapshot], "has_more": False}
            )
        if request.url.path == "/v1/batches/batch_owned/cancel":
            return httpx.Response(200, json={**snapshot, "status": "cancelling"})
        if request.url.path == "/v1/batches/batch_owned":
            return httpx.Response(200, json=snapshot)
        if request.url.path == "/v1/files/file-output/content":
            return httpx.Response(200, content=encoded(line(call)))
        assert request.url.path == "/v1/files/file-owned" and request.method == "DELETE"
        return httpx.Response(200, json={"id": "file-owned", "object": "file", "deleted": True})

    caplog.set_level("DEBUG")
    adapter = OpenAIBatchAdapter(
        api_key="synthetic-no-provider-key",
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    assert adapter.upload(blob, operation_id=operation)["sha256"] == file_sha256(blob)
    assert adapter.submit("file-owned", operation)["status"] == "in_progress"
    assert (
        adapter.poll("batch_owned", file_id="file-owned", operation_id=operation)["completed"] == 0
    )
    assert (
        adapter.cancel("batch_owned", file_id="file-owned", operation_id=operation)["status"]
        == "cancelling"
    )
    assert adapter.find_lost_submission("file-owned", operation)["batch_id"] == "batch_owned"
    assert result_files(adapter.download("file-output"), {call: model()})[call].complete
    adapter.delete_file("file-owned")
    adapter.close()
    assert len(requests) == 7
    assert "confidential" not in caplog.text and "synthetic-no-provider-key" not in caplog.text


def test_lost_submission_acknowledgement_or_provider_outage_is_never_retried():
    requests = []

    def fail(request):
        requests.append(request)
        raise httpx.ReadTimeout("owned synthetic timeout", request=request)

    adapter = OpenAIBatchAdapter(
        api_key="synthetic-no-provider-key",
        http_client=httpx.Client(transport=httpx.MockTransport(fail)),
    )
    from openai import APITimeoutError

    with pytest.raises(APITimeoutError):
        adapter.submit("file-owned", str(uuid4()))
    assert len(requests) == 1
    adapter.close()


@pytest.mark.parametrize("kind", ["wrong_name", "wrong_size", "duplicate", "incomplete"])
def test_read_only_upload_recovery_rejects_foreign_ambiguous_or_incomplete_identity(kind):
    operation = str(uuid4())
    seen = []
    item = {
        "id": "file-original",
        "object": "file",
        "bytes": 10,
        "created_at": 1,
        "filename": "cutback-" + operation + ".jsonl",
        "purpose": "batch",
    }

    def respond(request):
        seen.append(request)
        assert request.method == "GET" and request.url.path == "/v1/files"
        row = dict(item)
        if kind == "wrong_name":
            row["filename"] = "other-operation.jsonl"
        if kind == "wrong_size":
            row["bytes"] = 11
        if kind == "incomplete":
            row["filename"] = "unrelated.jsonl"
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [row, row] if kind == "duplicate" else [row],
                "has_more": kind == "incomplete",
            },
        )

    adapter = OpenAIBatchAdapter(
        api_key="synthetic-no-provider-key",
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    try:
        if kind == "wrong_name":
            assert adapter.find_lost_upload(operation, 10) is None
        else:
            with pytest.raises(ValueError, match="BATCH_"):
                adapter.find_lost_upload(operation, 10)
        assert len(seen) == (5 if kind == "incomplete" else 1)
    finally:
        adapter.close()
