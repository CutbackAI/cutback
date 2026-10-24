import hashlib
import hmac
import json
import time
from decimal import Decimal
from urllib.parse import parse_qs
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select

from cutback.server.enterprise_db import BillingAccount, HostedUsage


@pytest.fixture
def stripe_transport(monkeypatch):
    for key, value in {
        "CUTBACK_BILLING_ENABLED": "true",
        "CUTBACK_STRIPE_RESTRICTED_KEY": "rk_test_synthetic",
        "CUTBACK_STRIPE_WEBHOOK_SECRET": "whsec_synthetic",
        "CUTBACK_STRIPE_PRICE_IDS": "price_synthetic",
        "CUTBACK_PUBLIC_URL": "http://127.0.0.1:8080",
    }.items():
        monkeypatch.setenv(key, value)
    original = httpx.Client
    requests, status = [], {"value": "active"}

    def handle(request):
        requests.append(request)
        assert request.url.host == "api.stripe.com"
        assert request.headers["Stripe-Version"] == "2026-09-30.endive"
        path = request.url.path
        if path == "/v1/customers":
            return httpx.Response(200, json={"object": "customer", "id": "cus_synthetic"})
        if path == "/v1/checkout/sessions":
            params = parse_qs(request.content.decode())
            assert "payment_method_types[]" not in params
            assert params["line_items[0][price]"] == ["price_synthetic"]
            assert params["customer"] == ["cus_synthetic"]
            assert params["integration_identifier"][0].startswith("cutback-subscriptions-")
            assert len(params["integration_identifier"][0].split("-")[-1]) == 8
            return httpx.Response(
                200,
                json={
                    "object": "checkout.session",
                    "id": "cs_test_synthetic",
                    "url": "https://checkout.stripe.com/c/pay/cs_test_synthetic",
                },
            )
        if path == "/v1/billing_portal/sessions":
            return httpx.Response(
                200,
                json={
                    "object": "billing_portal.session",
                    "id": "bps_synthetic",
                    "url": "https://billing.stripe.com/p/session/synthetic",
                },
            )
        assert path == "/v1/subscriptions/sub_synthetic"
        return httpx.Response(
            200,
            json={
                "object": "subscription",
                "id": "sub_synthetic",
                "customer": "cus_synthetic",
                "status": status["value"],
                "items": {
                    "object": "list",
                    "data": [
                        {
                            "id": "si_synthetic",
                            "price": {"id": "price_synthetic", "object": "price"},
                        }
                    ],
                },
            },
        )

    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handle), **kw)
    )
    return requests, status


def webhook(client, event, *, secret="whsec_synthetic", valid=True):
    raw = json.dumps(event).encode()
    stamp = str(int(time.time()))
    signature = (
        hmac.new(secret.encode(), stamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
        if valid
        else "invalid"
    )
    return client.post(
        "/v1/billing/webhook",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "Stripe-Signature": f"t={stamp},v1={signature}",
        },
    )


@pytest.mark.postgres
def test_native_stripe_checkout_signed_idempotent_canonical_events(api, stripe_transport):
    client, admin, app = api
    requests, status = stripe_transport
    org = client.post(
        "/v1/organizations", headers=admin, json={"name": "Synthetic billing"}
    ).json()["id"]
    root = f"/v1/organizations/{org}/billing"
    request = {"price_id": "price_synthetic", "request_id": str(uuid4())}
    assert (
        client.post(
            root + "/checkout", headers=admin, json={**request, "price_id": "price_forbidden"}
        ).status_code
        == 422
    )
    assert not requests
    checkout = client.post(root + "/checkout", headers=admin, json=request)
    assert checkout.status_code == 200, checkout.text
    assert checkout.json()["checkout_url"].startswith("https://checkout.stripe.com/")
    assert client.get(root, headers=admin).json()["status"] == "unconfigured"
    event = {
        "id": "evt_synthetic",
        "object": "event",
        "created": 100,
        "type": "customer.subscription.updated",
        "livemode": False,
        "data": {"object": {"id": "sub_synthetic", "customer": "cus_synthetic"}},
    }
    assert webhook(client, event, valid=False).status_code == 400
    from cutback.server.billing import process_webhook

    raw = json.dumps(event).encode()
    stamp = str(int(time.time()))
    signature = hmac.new(
        b"whsec_synthetic", stamp.encode() + b"." + raw, hashlib.sha256
    ).hexdigest()
    assert (
        process_webhook(app.state.sessions, raw, f"t={stamp},v1={signature}")["status"]
        == "processed"
    )
    result = webhook(client, {**event, "id": "evt_http_positive"})
    assert result.status_code == 200, result.text
    assert client.get(root, headers=admin).json()["status"] == "active"
    before = len(requests)
    assert webhook(client, event).json()["status"] == "duplicate" and len(requests) == before
    status["value"] = "past_due"
    assert webhook(client, {**event, "id": "evt_older_timestamp", "created": 1}).status_code == 200
    assert client.get(root, headers=admin).json()["status"] == "past_due"
    assert (
        webhook(client, {**event, "id": "evt_unapproved_live", "livemode": True}).status_code == 403
    )
    assert client.post(root + "/portal", headers=admin, json={}).status_code == 200
    assert not client.get(root, headers=admin).json()["oss_license_required"]


@pytest.mark.postgres
def test_hosted_meter_atomic_cap_duplicate_handling_and_ungated_oss(api, event, monkeypatch):
    client, admin, app = api
    org = UUID(
        client.post("/v1/organizations", headers=admin, json={"name": "Synthetic metering"}).json()[
            "id"
        ]
    )
    project = client.post(
        "/v1/projects",
        headers=admin,
        json={"slug": "meter-" + uuid4().hex, "organization_id": str(org)},
    ).json()["id"]
    key = client.post(
        f"/v1/projects/{project}/keys", headers=admin, json={"role": "ingest"}
    ).json()["token"]
    writer = {"Authorization": "Bearer " + key}
    with app.state.sessions.begin() as session:
        session.add(
            BillingAccount(
                organization_id=org,
                status="active",
                monthly_event_cap=1,
                hosted_spend_cap_usd=Decimal("0.001"),
            )
        )
    monkeypatch.setenv("CUTBACK_HOSTED_MODE", "true")
    monkeypatch.setenv("CUTBACK_HOSTED_EVENT_RATE_USD", "0.001")
    body = {"project_id": project, "events": [event.model_dump(mode="json")]}
    assert client.post("/v1/ingest/events", headers=writer, json=body).json()["accepted"] == 1
    assert client.post("/v1/ingest/events", headers=writer, json=body).json()["duplicate"] == 1
    body["events"][0]["event_id"] = str(uuid4())
    assert client.post("/v1/ingest/events", headers=writer, json=body).status_code == 429
    with app.state.sessions() as session:
        usage = session.scalar(select(HostedUsage).where(HostedUsage.organization_id == org))
        assert usage.accepted_events == 1 and usage.reserved_hosted_usd == Decimal("0.001")
    monkeypatch.setenv("CUTBACK_HOSTED_MODE", "false")
    assert client.post("/v1/ingest/events", headers=writer, json=body).json()["accepted"] == 1
