"""Owned native HTTP batch fixture, with explicit synthetic answer rules.

No inference or customer-quality claim is made by this transport. Every HTTP
request is intercepted in memory, even though it uses the actual pinned SDK.
"""

import json
from email.parser import BytesParser
from email.policy import default
from typing import Any

import httpx

from cutback.evals.provider_batch_sdk import OpenAIBatchAdapter


def synthetic_batch_adapter(answer, *, lose_submit_ack=False, lose_upload_ack=False):
    files: dict[str, bytes] = {}
    metadata: dict[str, dict[str, Any]] = {}
    batches: dict[str, dict[str, Any]] = {}
    requests: list[tuple[str, str]] = []
    state = {"lost": False, "lost_upload": False, "submissions": 0, "uploads": 0}

    def respond(request):
        requests.append((request.method, request.url.path))
        assert request.url.host == "api.openai.com"
        path = request.url.path
        if path == "/v1/files" and request.method == "POST":
            message = BytesParser(policy=default).parsebytes(
                ("Content-Type: " + request.headers["content-type"] + "\r\n\r\n").encode()
                + request.content
            )
            part = next(part for part in message.iter_parts() if part.get_filename())
            blob = part.get_payload(decode=True)
            if not isinstance(blob, bytes):
                raise ValueError("OWNED_BATCH_MULTIPART_BYTES_REQUIRED")
            identifier = "file-input" + str(len(files))
            files[identifier] = blob
            state["uploads"] += 1
            metadata[identifier] = {
                "id": identifier,
                "object": "file",
                "bytes": len(blob),
                "created_at": 1,
                "filename": part.get_filename(),
                "purpose": "batch",
            }
            if lose_upload_ack and not state["lost_upload"]:
                state["lost_upload"] = True
                raise httpx.ReadTimeout("owned lost upload acknowledgement", request=request)
            return httpx.Response(
                200,
                json=metadata[identifier],
            )
        if path == "/v1/files" and request.method == "GET":
            return httpx.Response(
                200, json={"object": "list", "data": list(metadata.values()), "has_more": False}
            )
        if path == "/v1/batches" and request.method == "POST":
            value = json.loads(request.content)
            state["submissions"] += 1
            identifier = "batch_owned" + str(len(batches))
            output = "file-output" + str(len(batches))
            lines = []
            for row in map(json.loads, files[value["input_file_id"]].splitlines()):
                body = row["body"]
                lines.append(
                    {
                        "custom_id": row["custom_id"],
                        "response": {
                            "status_code": 200,
                            "request_id": "req_owned",
                            "body": {
                                "id": "resp_owned",
                                "object": "response",
                                "created_at": 1,
                                "model": body["model"],
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
                                        "content": [
                                            {
                                                "type": "output_text",
                                                "text": answer(body["input"]),
                                                "annotations": [],
                                            }
                                        ],
                                    }
                                ],
                                "usage": {
                                    "input_tokens": 20,
                                    "output_tokens": 1,
                                    "total_tokens": 21,
                                    "input_tokens_details": {"cached_tokens": 0},
                                    "output_tokens_details": {"reasoning_tokens": 0},
                                },
                            },
                        },
                        "error": None,
                    }
                )
            files[output] = b"".join(json.dumps(row).encode() + b"\n" for row in reversed(lines))
            snapshot = {
                "id": identifier,
                "object": "batch",
                "endpoint": "/v1/responses",
                "input_file_id": value["input_file_id"],
                "completion_window": "24h",
                "created_at": 1,
                "status": "in_progress",
                "metadata": value["metadata"],
                "output_file_id": output,
                "request_counts": {"total": len(lines), "completed": 0, "failed": 0},
            }
            batches[identifier] = snapshot
            if lose_submit_ack and not state["lost"]:
                state["lost"] = True
                raise httpx.ReadTimeout("owned lost acknowledgement", request=request)
            return httpx.Response(200, json=snapshot)
        if path == "/v1/batches":
            return httpx.Response(
                200, json={"object": "list", "data": list(batches.values()), "has_more": False}
            )
        if path.startswith("/v1/batches/"):
            identifier = path.split("/")[3]
            value = batches[identifier]
            value["status"] = "cancelled" if path.endswith("/cancel") else "completed"
            value["request_counts"]["completed"] = value["request_counts"]["total"]
            return httpx.Response(200, json=value)
        if path.endswith("/content"):
            return httpx.Response(200, content=files[path.split("/")[3]])
        if path.startswith("/v1/files/") and request.method == "GET":
            return httpx.Response(200, json=metadata[path.split("/")[3]])
        assert request.method == "DELETE" and path.startswith("/v1/files/")
        identifier = path.split("/")[3]
        files.pop(identifier, None)
        metadata.pop(identifier, None)
        return httpx.Response(200, json={"id": identifier, "object": "file", "deleted": True})

    adapter = OpenAIBatchAdapter(
        api_key="synthetic-no-provider-key",
        http_client=httpx.Client(transport=httpx.MockTransport(respond), trust_env=False),
    )
    adapter.synthetic_transport = True
    return adapter, state, requests
