"""Explicit per-operation native SDK headers. Contains no SDK or server imports."""

from collections.abc import Mapping
from uuid import UUID, uuid4


def operation_headers(operation_id: UUID | None = None):
    """Reuse this exact operation ID across retries; never reuse it for new work.

    A gateway never persists responses for replay. A duplicate may return a
    nonretryable conflict after the original request has already been charged.
    """
    if operation_id is None:
        operation_id = uuid4()
    if not isinstance(operation_id, UUID) or operation_id.int == 0:
        raise ValueError("GATEWAY_NONZERO_OPERATION_UUID_REQUIRED")
    return {
        "X-Cutback-Operation-ID": str(operation_id),
        "X-Cutback-Observation-Delegate": "gateway-native-text-v1",
    }


def delegated(resource, kwargs):
    """Only explicit virtual-key requests to a custom gateway delegate observation."""
    client = resource._client
    if (
        type(client).__name__ not in {"OpenAI", "AsyncOpenAI"}
        or type(kwargs.get("stream", False)) is not bool
        or kwargs.get("model") != "gpt-6-luna"
        or kwargs.get("store") is not False
        or kwargs.get("service_tier") != "default"
        or not isinstance(kwargs.get("input"), str)
    ):
        return False
    if client.base_url.host == "api.openai.com" or not isinstance(client.api_key, str):
        return False
    if not client.api_key.startswith("cbg_"):
        return False
    headers = kwargs.get("extra_headers")
    if not isinstance(headers, Mapping):
        return False
    headers = {name.lower(): value for name, value in headers.items()}
    if headers.get("x-cutback-observation-delegate") != "gateway-native-text-v1":
        return False
    try:
        return UUID(headers.get("x-cutback-operation-id", "")).int != 0
    except (ValueError, TypeError, AttributeError):
        return False
