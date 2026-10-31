"""Authentic synthetic past-expiry objects; never alter the system clock."""

import base64
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest


def expired_object(store, project, *, ref=None):
    ref = ref or str(uuid4())
    header = json.dumps(
        {
            "project": str(project),
            "ref": ref,
            "expires": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        },
        sort_keys=True,
    ).encode()
    nonce = os.urandom(12)
    raw = json.dumps(
        {
            "header": base64.b64encode(header).decode(),
            "nonce": base64.b64encode(nonce).decode(),
            "ciphertext": base64.b64encode(
                store.cipher.encrypt(nonce, b"owned private synthetic payload", header)
            ).decode(),
        }
    ).encode()
    path = store._path(project, ref)
    path.write_bytes(raw)
    return ref, path


def drain(store, **options):
    receipts = []
    for _ in range(30):
        receipt = store.expire_page(**options)
        receipts.append(receipt)
        if receipt["cycle_complete"]:
            return receipts
    pytest.fail("bounded sweep did not finish the owned fixture")
