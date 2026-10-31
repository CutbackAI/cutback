"""Local retrieval and native SDK consumer checks. Every HTTP reply is synthetic."""

import json
from importlib.metadata import version

from app import answer, build_input


def main():
    frame = build_input("refund return deadline for amber")
    assert "deadline-days=14" in frame, "Retrieval lost the independent synthetic answer fact"
    assert len(frame.encode()) < 16000
    if version("openai") not in {"2.29.0", "3.35.1"}:
        raise ValueError("SOURCE_QUALIFIED_OPENAI_SDK_REQUIRED")
    import httpx
    from openai import APIStatusError, OpenAI

    calls = []

    def transport(request):
        assert str(request.url) == "https://api.openai.com/v1/responses"
        body = json.loads(request.content)
        assert body["model"] in {"gpt-4.1", "gpt-4.1-mini"}
        assert type(body["max_output_tokens"]) is int and 16 <= body["max_output_tokens"] <= 16384
        assert body["input"] == frame and body["store"] is False
        calls.append(1)
        if len(calls) > 1:
            return httpx.Response(503, json={"error": {"message": "owned synthetic outage"}})
        return httpx.Response(
            200,
            json={
                "id": "resp_owned_consumer",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": body["model"],
                "output": [
                    {
                        "id": "msg_owned",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "14", "annotations": []}],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
            },
        )

    with OpenAI(
        api_key="synthetic-no-provider-key",
        max_retries=0,
        http_client=httpx.Client(
            transport=httpx.MockTransport(transport), trust_env=False, follow_redirects=False
        ),
    ) as client:
        assert answer(client, "refund return deadline for amber") == "14"
        try:
            answer(client, "refund return deadline for amber")
        except APIStatusError as exc:
            assert exc.status_code == 503
        else:
            raise AssertionError("Native SDK outage must remain visible")
    assert len(calls) == 2


if __name__ == "__main__":
    main()
