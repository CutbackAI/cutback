"""Shared hard deny and content handling for API and background artifact paths."""

import json
import os

from cutback.evals.datasets import redact


def require_payloads_enabled():
    if os.getenv("CUTBACK_ALLOW_PAYLOADS", "false").lower() != "true":
        raise ValueError("PAYLOAD_STORAGE_DISABLED")


def policy_content(value, policy, *, preserve_semantics=False):
    require_payloads_enabled()
    clean = redact(value, policy.never_capture_fields)
    if preserve_semantics and clean != value:
        raise ValueError("ARTIFACT_REQUIRES_APPLICATION_DEIDENTIFICATION")
    blob = json.dumps(clean, ensure_ascii=True).encode()
    if len(blob) > policy.max_payload_bytes:
        raise ValueError("CONTENT_EXCEEDS_POLICY_BYTES")
    return clean, blob
