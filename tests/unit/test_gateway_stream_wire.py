"""Wire bounds, immutable legacy policy hashes and explicit stream authorization."""

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from cutback.core.native_models import NativeModelContract
from cutback.gateway.app import strict_json
from cutback.gateway.contracts import GatewayPolicy, ResponsesStream, qualify_request
from cutback.gateway.streaming import Frames
from cutback.server.gateway_service import GatewayError


def legacy_policy():
    limit = dict(modeled_usd="10", requests=20, concurrent_requests=4)
    return GatewayPolicy(
        policy_id=UUID(int=1),
        environment_ids=[UUID(int=2)],
        starts_at=datetime(2026, 10, 29, tzinfo=UTC),
        expires_at=datetime(2026, 10, 30, tzinfo=UTC),
        project_limit=limit,
        teams=[dict(team_id=UUID(int=3), limit=limit)],
        models=[
            dict(
                price_contract=NativeModelContract.current_luna_text(reasoning_effort="medium"),
                limit=limit,
            )
        ],
    )


def test_released_policy_hash_is_unchanged_and_streams_need_new_approval():
    legacy = legacy_policy()
    # Calculated independently using the accepted pre-streaming installed wheel.
    assert (
        legacy.approval_hash() == "e931b9ddeec5d17df09d2ab5b84e3ace122038ff136f90e73cd4e8a2e4176cf9"
    )
    request = ResponsesStream(
        model="gpt-6-luna",
        input="owned text",
        max_output_tokens=128,
        store=False,
        service_tier="default",
        stream=True,
    )
    with pytest.raises(ValueError, match="OUTSIDE_APPROVED"):
        qualify_request(request, legacy.models[0])
    values = legacy.model_dump(mode="python")
    values["version"] = "cutback.gateway-policy.v2"
    with pytest.raises(ValidationError, match="VERSION_MODEL"):
        GatewayPolicy.model_validate(values)
    values["models"][0]["key"] = "openai-luna-responses-text-stream-v1"
    stream_policy = GatewayPolicy.model_validate(values)
    qualify_request(request, stream_policy.models[0])
    assert stream_policy.approval_hash() != legacy.approval_hash()
    # No separate streaming model bucket can split one SKU's approved allowance.
    values["models"].append(legacy.models[0].model_dump(mode="python"))
    with pytest.raises(ValidationError):
        GatewayPolicy.model_validate(values)


@pytest.mark.parametrize(
    "change",
    [
        {"stream": 1},
        {"stream": False},
        {"store": 0},
        {"store": True},
        {"stream_options": {"include_usage": True}},
        {"stream_options": {"include_obfuscation": 1}},
        {"background": True},
        {"tools": []},
    ],
)
def test_stream_options_do_not_coerce_or_add_unqualified_surfaces(change):
    with pytest.raises(ValidationError):
        ResponsesStream.model_validate(
            dict(
                model="gpt-6-luna",
                input="owned text",
                max_output_tokens=128,
                store=False,
                service_tier="default",
                stream=True,
            )
            | change
        )


@pytest.mark.parametrize("ending", [b"\n", b"\r\n", b"\r"])
def test_every_byte_fragment_preserves_native_wire_and_ignores_comments(ending):
    wire = ending.join(
        [
            b": private transient comment",
            b"",
            b"event: response.completed",
            b'data: {"type":"response.completed",',
            b'data: "response":{}}',
            b"",
            b"data: [DONE]",
            b"",
            b"",
        ]
    )
    frames = Frames(strict_json)
    emitted = []
    for byte in wire:
        emitted.extend(frames.feed(bytes([byte])))
    frames.end()
    assert b"".join(frame for frame, _ in emitted) == wire
    assert [event["type"] for _, event in emitted if event] == ["response.completed"]


@pytest.mark.parametrize(
    "wire",
    [
        b"data: [DONE]\n\n",
        b'data: {"type":"a","type":"b"}\n\n',
        b'event: different\ndata: {"type":"response.completed"}\n\n',
        b"data: []\n\n",
        b'data: {"type":"response.output_item.added","item":{"type":"function_call"}}\n\n',
    ],
)
def test_unsafe_or_incomplete_wire_fails_with_no_raw_content(wire):
    with pytest.raises(GatewayError) as error:
        Frames(strict_json).feed(wire)
    assert "data:" not in str(error.value)


@pytest.mark.parametrize("bound", ["bytes", "frame", "events"])
def test_each_stream_bound_is_enforced(monkeypatch, bound):
    from cutback.gateway import streaming

    frames = Frames(strict_json)
    if bound == "bytes":
        monkeypatch.setattr(streaming, "MAX_BYTES", 8)
        raw = b": comment" * 2
    elif bound == "frame":
        monkeypatch.setattr(streaming, "MAX_FRAME", 8)
        raw = b": comment too large\n\n"
    else:
        monkeypatch.setattr(streaming, "MAX_EVENTS", 1)
        raw = b'data: {"type":"response.created"}\n\n' * 2
    with pytest.raises(GatewayError):
        frames.feed(raw)


@pytest.mark.parametrize("character", ["\u0085", "\u2028", "\u2029"])
def test_unicode_text_line_separators_are_not_sse_protocol_newlines(character):
    event = {"type": "response.output_text.delta", "delta": "alpha" + character + "beta"}
    raw = ("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode()
    frames = Frames(strict_json)
    emitted = []
    for byte in raw:
        emitted.extend(frames.feed(bytes([byte])))
    assert emitted == [(raw, event)]
