"""Optional customer-configured hosted subscriptions. Fundamental OSS paths have no license gate."""

import os
import string
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from cutback.core.config import validate_endpoint
from cutback.core.models import StrictModel
from cutback.server.db import AuditEvent, Organization, Project
from cutback.server.enterprise_db import BillingAccount, BillingEvent, HostedUsage
from cutback.server.organization_api import organization_scope


def stripe_return_url(value, hostname):
    url = urlsplit(value or "")
    if (
        url.scheme != "https"
        or url.hostname != hostname
        or url.username
        or url.password
        or url.port not in {None, 443}
    ):
        raise HTTPException(502, "UNEXPECTED_STRIPE_RETURN_URL")
    return value


def billing_enabled():
    return os.getenv("CUTBACK_BILLING_ENABLED", "false").lower() == "true"


def allowed_prices():
    return [
        item
        for item in os.getenv("CUTBACK_STRIPE_PRICE_IDS", "").split(",")
        if item.startswith("price_")
    ][:20]


@contextmanager
def configured_stripe():
    if not billing_enabled():
        raise HTTPException(503, "BILLING_NOT_ENABLED")
    key = os.getenv("CUTBACK_STRIPE_RESTRICTED_KEY", "")
    if not key.startswith(("rk_test_", "sk_test_", "rk_live_", "sk_live_")):
        raise HTTPException(503, "STRIPE_KEY_NOT_CONFIGURED")
    if "_live_" in key and os.getenv("CUTBACK_BILLING_ALLOW_LIVE", "false").lower() != "true":
        raise HTTPException(403, "LIVE_BILLING_NOT_APPROVED")
    try:
        import httpx
        import stripe
    except ImportError:
        raise HTTPException(503, "COMMERCIAL_EXTRA_REQUIRED") from None

    class Transport(stripe.HTTPClient):
        name = "cutback-bounded-httpx"

        def __init__(self):
            super().__init__()
            self.client = httpx.Client(timeout=5, follow_redirects=False, trust_env=False)

        def request(self, method, url, headers, post_data=None, *, _usage=None):
            with self.client.stream(method, url, headers=headers, content=post_data) as response:
                body = bytearray()
                for chunk in response.iter_bytes():
                    if len(body) + len(chunk) > 1048576:
                        raise ValueError("STRIPE_RESPONSE_TOO_LARGE")
                    body.extend(chunk)
                return body.decode(), response.status_code, dict(response.headers)

        def close(self):
            self.client.close()

    transport = Transport()
    try:
        # Stripe 16.0.0 pins 2026-09-30.endive. No hidden retries of a financial operation.
        yield stripe.StripeClient(
            key, stripe_version="2026-09-30.endive", max_network_retries=0, http_client=transport
        )
    finally:
        transport.close()


def meter_ingestion(session, project_id, accepted):
    if not accepted:
        return
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "PROJECT_NOT_FOUND")
    period = datetime.now(UTC).strftime("%Y-%m")
    session.execute(
        insert(HostedUsage)
        .values(
            organization_id=project.organization_id,
            period=period,
            accepted_events=0,
            reserved_hosted_usd=Decimal(0),
        )
        .on_conflict_do_nothing(index_elements=["organization_id", "period"])
    )
    usage = session.scalar(
        select(HostedUsage)
        .where(HostedUsage.organization_id == project.organization_id, HostedUsage.period == period)
        .with_for_update()
    )
    account = session.get(BillingAccount, project.organization_id)
    rate = os.getenv("CUTBACK_HOSTED_EVENT_RATE_USD")
    unit = Decimal(rate) if rate is not None else None
    if unit is not None and (not unit.is_finite() or unit < 0):
        raise HTTPException(503, "INVALID_HOSTED_RATE_CONFIGURATION")
    hosted = os.getenv("CUTBACK_HOSTED_MODE", "false").lower() == "true"
    if hosted:
        if not account or account.status not in {"active", "trialing"}:
            raise HTTPException(403, "HOSTED_SUBSCRIPTION_REQUIRED")
        if (
            account.monthly_event_cap is not None
            and usage.accepted_events + accepted > account.monthly_event_cap
        ):
            raise HTTPException(429, "HOSTED_MONTHLY_EVENT_CAP_REACHED")
        if account.hosted_spend_cap_usd is not None:
            if unit is None:
                raise HTTPException(503, "HOSTED_RATE_REQUIRED_FOR_SPEND_CAP")
            if usage.reserved_hosted_usd + unit * accepted > account.hosted_spend_cap_usd:
                raise HTTPException(429, "HOSTED_SPEND_CAP_REACHED")
    usage.accepted_events += accepted
    if unit is not None:
        usage.reserved_hosted_usd += unit * accepted


class CheckoutInput(StrictModel):
    price_id: str = Field(pattern=r"^price_[A-Za-z0-9]+$", max_length=200)
    request_id: UUID


class BillingCaps(StrictModel):
    monthly_event_cap: int | None = Field(default=None, ge=1, le=1000000000)
    hosted_spend_cap_usd: Decimal | None = Field(default=None, ge=0, le=1000000)


def install_billing(app, sessions, authorize, scope):
    @app.get("/v1/organizations/{organization_id}/billing")
    def billing(organization_id: UUID, auth=Depends(authorize)):
        organization_scope(auth, organization_id, manage=True)
        with sessions() as session:
            account = session.get(BillingAccount, organization_id)
            usage = session.scalar(
                select(HostedUsage).where(
                    HostedUsage.organization_id == organization_id,
                    HostedUsage.period == datetime.now(UTC).strftime("%Y-%m"),
                )
            )
            return {
                "enabled": billing_enabled(),
                "hosted_mode": os.getenv("CUTBACK_HOSTED_MODE", "false").lower() == "true",
                "status": account.status if account else "unconfigured",
                "price_ids": allowed_prices(),
                "price_id": account.price_id if account else None,
                "period": datetime.now(UTC).strftime("%Y-%m"),
                "accepted_events": usage.accepted_events if usage else 0,
                "modeled_hosted_subtotal_usd": str(usage.reserved_hosted_usd)
                if usage and os.getenv("CUTBACK_HOSTED_EVENT_RATE_USD")
                else None,
                "invoice_total_usd": None,
                "monthly_event_cap": account.monthly_event_cap if account else None,
                "hosted_spend_cap_usd": str(account.hosted_spend_cap_usd)
                if account and account.hosted_spend_cap_usd is not None
                else None,
                "oss_license_required": False,
            }

    @app.put("/v1/organizations/{organization_id}/billing/caps")
    def caps(organization_id: UUID, body: BillingCaps, auth=Depends(authorize)):
        organization_scope(auth, organization_id, owner=True)
        with sessions.begin() as session:
            if not session.get(Organization, organization_id):
                raise HTTPException(404, "ORGANIZATION_NOT_FOUND")
            row = session.get(BillingAccount, organization_id, with_for_update=True)
            if not row:
                row = BillingAccount(organization_id=organization_id)
                session.add(row)
            row.monthly_event_cap, row.hosted_spend_cap_usd = (
                body.monthly_event_cap,
                body.hosted_spend_cap_usd,
            )
            session.add(
                AuditEvent(
                    actor=auth["actor"],
                    action="hosted_caps_updated",
                    details={
                        "organization_id": str(organization_id),
                        **body.model_dump(mode="json"),
                    },
                )
            )
        return {"status": "saved", "enforcement": "hosted mode only; fundamental OSS is ungated"}

    @app.post("/v1/organizations/{organization_id}/billing/checkout")
    def checkout(organization_id: UUID, body: CheckoutInput, auth=Depends(authorize)):
        organization_scope(auth, organization_id, owner=True)
        if body.price_id not in allowed_prices():
            raise HTTPException(422, "PRICE_NOT_IN_SERVER_ALLOWLIST")
        public = os.getenv("CUTBACK_PUBLIC_URL")
        if not public:
            raise HTTPException(503, "PUBLIC_URL_NOT_CONFIGURED")
        public = validate_endpoint(public)
        with sessions.begin() as session:
            organization = session.get(Organization, organization_id, with_for_update=True)
            if not organization:
                raise HTTPException(404, "ORGANIZATION_NOT_FOUND")
            account = session.get(BillingAccount, organization_id)
            if not account:
                account = BillingAccount(organization_id=organization_id)
                session.add(account)
            if account.status in {"active", "trialing"}:
                raise HTTPException(409, "USE_EXISTING_SUBSCRIPTION_PORTAL")
            try:
                with configured_stripe() as client:
                    if not account.customer_id:
                        customer = client.v1.customers.create(
                            params={"metadata": {"cutback_organization": str(organization_id)}},
                            options={"idempotency_key": "cutback-customer-" + str(organization_id)},
                        )
                        account.customer_id = customer.id
                    params: dict[str, Any] = {
                        "mode": "subscription",
                        "customer": account.customer_id,
                        "line_items": [{"price": body.price_id, "quantity": 1}],
                        "client_reference_id": str(organization_id),
                        "subscription_data": {
                            "metadata": {"cutback_organization": str(organization_id)}
                        },
                        "success_url": public + "/dashboard/?billing=returned",
                        "cancel_url": public + "/dashboard/?billing=cancelled",
                        "integration_identifier": "cutback-subscriptions-"
                        + "".join(
                            string.ascii_lowercase[b % 26] for b in body.request_id.bytes[:8]
                        ),
                    }
                    result = client.v1.checkout.sessions.create(
                        params=params,
                        options={
                            "idempotency_key": "cutback-checkout-"
                            + str(organization_id)
                            + "-"
                            + str(body.request_id)
                        },
                    )
                    session.add(
                        AuditEvent(
                            actor=auth["actor"],
                            action="subscription_checkout_created",
                            details={
                                "organization_id": str(organization_id),
                                "request_id": str(body.request_id),
                                "price_id": body.price_id,
                            },
                        )
                    )
                    return {
                        "checkout_url": stripe_return_url(result.url, "checkout.stripe.com"),
                        "fulfillment": "signed canonical webhook state only",
                    }
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(
                    502, "STRIPE_OPERATION_UNCERTAIN_RETRY_SAME_REQUEST_ID"
                ) from None

    @app.post("/v1/organizations/{organization_id}/billing/portal")
    def portal(organization_id: UUID, auth=Depends(authorize)):
        organization_scope(auth, organization_id, owner=True)
        public = os.getenv("CUTBACK_PUBLIC_URL")
        if not public:
            raise HTTPException(503, "PUBLIC_URL_NOT_CONFIGURED")
        with sessions.begin() as session:
            account = session.get(BillingAccount, organization_id)
            if not account or not account.customer_id:
                raise HTTPException(409, "BILLING_CUSTOMER_NOT_CONFIGURED")
            with configured_stripe() as client:
                try:
                    result = client.v1.billing_portal.sessions.create(
                        params={
                            "customer": account.customer_id,
                            "return_url": validate_endpoint(public) + "/dashboard/",
                        }
                    )
                except Exception:
                    raise HTTPException(502, "BILLING_PORTAL_UNAVAILABLE") from None
            session.add(
                AuditEvent(
                    actor=auth["actor"],
                    action="billing_portal_link_created",
                    details={"organization_id": str(organization_id)},
                )
            )
        return {"portal_url": stripe_return_url(result.url, "billing.stripe.com")}

    @app.post("/v1/billing/webhook")
    async def webhook(request: Request):
        from fastapi.concurrency import run_in_threadpool

        return await run_in_threadpool(
            process_webhook,
            sessions,
            await request.body(),
            request.headers.get("stripe-signature", ""),
        )


def process_webhook(sessions, raw, signature):
    secret = os.getenv("CUTBACK_STRIPE_WEBHOOK_SECRET")
    if not secret or not billing_enabled():
        raise HTTPException(503, "BILLING_WEBHOOK_NOT_CONFIGURED")
    try:
        import stripe

        event = stripe.Webhook.construct_event(raw, signature, secret).to_dict()
        if (
            not isinstance(event.get("id"), str)
            or len(event["id"]) > 200
            or not isinstance(event.get("type"), str)
        ):
            raise ValueError("invalid event identity")
    except Exception:
        raise HTTPException(400, "INVALID_BILLING_SIGNATURE") from None
    if event.get("livemode") and os.getenv("CUTBACK_BILLING_ALLOW_LIVE", "false").lower() != "true":
        raise HTTPException(403, "LIVE_BILLING_NOT_APPROVED")
    supported = {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "invoice.paid",
        "invoice.payment_failed",
    }
    if event["type"] not in supported:
        return {"status": "ignored"}
    obj = event["data"]["object"]
    customer = obj.get("customer")
    with sessions.begin() as session:
        if session.get(BillingEvent, event["id"]):
            return {"status": "duplicate"}
        account = session.scalar(
            select(BillingAccount).where(BillingAccount.customer_id == customer).with_for_update()
        )
        if not customer or not account:
            raise HTTPException(503, "BILLING_CUSTOMER_RECONCILIATION_REQUIRED")
        if session.get(BillingEvent, event["id"]):
            return {"status": "duplicate"}
        # Do not use event.created for ordering: distinct events can share timestamps.
        subscription = (
            obj.get("id")
            if event["type"].startswith("customer.subscription.")
            else obj.get("subscription")
        )
        if not subscription and event["type"].startswith("invoice."):
            subscription = obj.get("parent", {}).get("subscription_details", {}).get("subscription")
        if event["type"].startswith("checkout.") and obj.get("payment_status") not in {
            "paid",
            "no_payment_required",
        }:
            return {"status": "awaiting_payment"}
        if not subscription:
            raise HTTPException(503, "SUBSCRIPTION_RECONCILIATION_REQUIRED")
        try:
            with configured_stripe() as client:
                canonical = client.v1.subscriptions.retrieve(subscription).to_dict()
            if canonical["customer"] != account.customer_id:
                raise ValueError("customer mismatch")
            items = canonical.get("items", {}).get("data", [])
            price = items[0].get("price", {}).get("id") if items else None
            if price not in allowed_prices():
                raise ValueError("unapproved price")
            account.subscription_id, account.status, account.price_id = (
                canonical["id"],
                canonical["status"],
                price,
            )
            account.last_event_at = datetime.now(UTC)
        except Exception:
            raise HTTPException(503, "CANONICAL_SUBSCRIPTION_UNAVAILABLE") from None
        session.add(
            BillingEvent(
                event_id=event["id"],
                organization_id=account.organization_id,
                event_type=event["type"],
            )
        )
        session.add(
            AuditEvent(
                actor="stripe_signed_webhook",
                action="billing_subscription_reconciled",
                details={
                    "organization_id": str(account.organization_id),
                    "event_id": event["id"],
                    "status": account.status,
                },
            )
        )
    return {"status": "processed"}
