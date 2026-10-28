import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from cutback.core.prompts import PromptDocument, PromptError, fingerprint
from cutback.prompts import PromptClient


def response_body():
    root = str(uuid4())
    document = PromptDocument(text="Classify {{input}}", variables={"input": "string"}).model_dump(
        mode="json"
    )
    receipt = {"receipt_nonce": uuid4().hex, "request_fingerprint": "a" * 64}
    hashed = fingerprint({"document": document, **receipt})
    return {
        "root_version_id": root,
        "documents": {root: document},
        "content_receipts": {root: receipt},
        "version_hashes": {root: hashed},
        "version_hash": hashed,
        "expires_at": (datetime.now(UTC) + timedelta(minutes=2)).isoformat(),
        "alias": None,
    }


def test_reader_keeps_values_local_and_does_not_cache_or_retry():
    raw, calls = response_body(), []

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer synthetic-reader"
        assert json.loads(request.content)["provider"] == "local"
        assert "LOCAL_PRIVATE_VALUE" not in request.content.decode()
        return (
            httpx.Response(200, json=raw)
            if len(calls) == 1
            else httpx.Response(403, text="PRIVATE_ERROR")
        )

    with PromptClient(
        "http://127.0.0.1:8777", uuid4(), "synthetic-reader", transport=httpx.MockTransport(handler)
    ) as client:
        resolved = client.resolve(
            "sample",
            environment="staging",
            workflow="classify",
            provider="local",
            purpose="owned-synthetic",
            version_id=raw["root_version_id"],
        )
        assert resolved.render({"input": "LOCAL_PRIVATE_VALUE"}) == {
            "kind": "text",
            "text": "Classify LOCAL_PRIVATE_VALUE",
        }
        with pytest.raises(PromptError, match="DENIED_OR_UNAVAILABLE") as exc:
            client.resolve(
                "sample",
                environment="staging",
                workflow="classify",
                provider="local",
                purpose="owned-synthetic",
                version_id=raw["root_version_id"],
            )
        assert "PRIVATE_ERROR" not in str(exc.value) and len(calls) == 2


@pytest.mark.parametrize("corruption", ["hash", "expiry", "document", "extra", "redirect", "huge"])
def test_reader_rejects_tampered_expired_redirected_and_unbounded_content(corruption):
    raw = response_body()
    root = raw["root_version_id"]
    if corruption == "hash":
        raw["version_hash"] = "b" * 64
    elif corruption == "expiry":
        raw["expires_at"] = datetime.now(UTC).isoformat()
    elif corruption == "document":
        raw["documents"][root]["text"] = "changed {{input}}"
    elif corruption == "extra":
        raw["documents"][str(uuid4())] = raw["documents"][root]
    calls = []

    def handler(request):
        calls.append(request)
        if corruption == "redirect":
            return httpx.Response(307, headers={"location": "https://example.invalid/leak"})
        if corruption == "huge":
            return httpx.Response(200, content=b"x" * 1048577)
        return httpx.Response(200, json=raw)

    with PromptClient(
        "https://example.invalid",
        uuid4(),
        "synthetic-reader",
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(PromptError):
            client.resolve(
                "sample",
                environment="staging",
                workflow="classify",
                provider="local",
                purpose="owned-synthetic",
                version_id=root,
            )
    assert len(calls) == 1
