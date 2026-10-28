"""Receiver-side signature verification only. Persistent delivery deduplication is caller-owned."""

import hashlib
import hmac
import math
import re
import time
from uuid import UUID


def verify_webhook_signature(
    raw_body: bytes,
    *,
    delivery_id: str,
    timestamp: str,
    signature: str,
    secret: str,
    now: float | None = None,
    max_age_seconds: int = 300,
) -> bool:
    """Authenticate exact bytes, timestamp AND delivery identity; performs no action.

    After verification, validate the metadata schema/project and atomically deduplicate
    delivery_id in your own durable store before handling the transition. This helper
    does not attest task quality, persist state, or make a network request.
    """
    if (
        not isinstance(raw_body, bytes)
        or len(raw_body) > 65536
        or not isinstance(secret, str)
        or not 32 <= len(secret) <= 4096
        or not isinstance(delivery_id, str)
        or len(delivery_id) != 36
        or not isinstance(timestamp, str)
        or not re.fullmatch(r"[0-9]{1,13}", timestamp)
        or not isinstance(signature, str)
        or not re.fullmatch(r"sha256=[a-f0-9]{64}", signature)
        or type(max_age_seconds) is not int
        or not 1 <= max_age_seconds <= 3600
    ):
        return False
    try:
        if str(UUID(delivery_id)) != delivery_id or UUID(delivery_id).int == 0:
            return False
        at = int(timestamp)
        clock = time.time() if now is None else now
        if (
            not isinstance(clock, (float, int))
            or not math.isfinite(clock)
            or str(at) != timestamp
            or not -60 <= clock - at <= max_age_seconds
        ):
            return False
    except (ValueError, OverflowError):
        return False
    expected = hmac.new(
        secret.encode(),
        timestamp.encode() + b"." + delivery_id.encode() + b"." + raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, "sha256=" + expected)
