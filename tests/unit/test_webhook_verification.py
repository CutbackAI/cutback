import hashlib
import hmac

from cutback.webhooks import verify_webhook_signature


def test_receiver_verification_authenticates_delivery_identity_and_replay_window():
    body = b'{"schema":"cutback.incident.v1"}'
    identifier = "00000000-0000-0000-0000-000000000001"
    secret = "owned-synthetic-signature-" + "x" * 32
    signature = (
        "sha256="
        + hmac.new(
            secret.encode(), b"1000." + identifier.encode() + b"." + body, hashlib.sha256
        ).hexdigest()
    )
    options = dict(
        delivery_id=identifier, timestamp="1000", signature=signature, secret=secret, now=1100
    )
    assert verify_webhook_signature(body, **options)
    assert not verify_webhook_signature(body + b" ", **options)
    assert not verify_webhook_signature(
        body, **{**options, "delivery_id": "00000000-0000-0000-0000-000000000002"}
    )
    assert not verify_webhook_signature(body, **{**options, "timestamp": "1001"})
    assert not verify_webhook_signature(body, **{**options, "now": 1301})
    assert not verify_webhook_signature(body, **{**options, "now": 900})
    assert not verify_webhook_signature(body, **{**options, "timestamp": "01000"})
    assert not verify_webhook_signature(
        body, **{**options, "secret": "different-synthetic-secret-" + "x" * 32}
    )
    assert not verify_webhook_signature(body, **{**options, "max_age_seconds": 0})
