"""Explicitly configured metadata webhooks. No DNS, redirects, ambient auth or proxies."""

import hashlib
import hmac
import ipaddress
import json
import os
import re
import ssl
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpcore
from httpcore._backends.sync import SyncBackend
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert

from cutback.server.monitoring_db import NotificationConfig, NotificationDelivery


def destinations():
    from cutback.server.runtime import load_secret

    try:
        load_secret("CUTBACK_WEBHOOK_DESTINATIONS")
    except (OSError, ValueError):
        raise ValueError("WEBHOOK_OPERATOR_CONFIGURATION_INVALID") from None
    raw = os.getenv("CUTBACK_WEBHOOK_DESTINATIONS", "{}")
    if len(raw) > 32768:
        raise ValueError("WEBHOOK_OPERATOR_CONFIGURATION_INVALID")
    try:
        values = json.loads(raw)
        if not isinstance(values, dict) or len(values) > 100:
            raise ValueError
        result = {}
        for key, entry in values.items():
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", key) or set(entry) != {
                "url",
                "addresses",
                "secret_env",
                "project_ids",
            }:
                raise ValueError
            projects = entry["project_ids"]
            if (
                not isinstance(projects, list)
                or not 1 <= len(projects) <= 100
                or len(set(projects)) != len(projects)
            ):
                raise ValueError
            if any(str(UUID(value)) != value or not UUID(value).int for value in projects):
                raise ValueError
            url = entry["url"]
            parts = urlsplit(url)
            if (
                len(url) > 2048
                or not url.isascii()
                or any(ord(c) <= 32 for c in url)
                or parts.scheme != "https"
                or not parts.hostname
                or parts.port not in {None, 443}
                or parts.username is not None
                or parts.password is not None
                or parts.query
                or parts.fragment
                or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", parts.hostname)
                or any(not label or len(label) > 63 for label in parts.hostname.split("."))
            ):
                raise ValueError
            addresses = entry["addresses"]
            if not isinstance(addresses, list) or not 1 <= len(addresses) <= 4:
                raise ValueError
            for value in addresses:
                if not isinstance(value, str):
                    raise ValueError
                address = ipaddress.ip_address(value)
                if (
                    not address.is_global
                    or address.is_multicast
                    or address.is_unspecified
                    or address.is_loopback
                    or address.is_reserved
                    or address.is_link_local
                ):
                    raise ValueError
                if isinstance(address, ipaddress.IPv6Address) and (
                    address not in ipaddress.ip_network("2000::/3")
                    or address.sixtofour
                    or address.teredo
                    or address.ipv4_mapped
                    or address.scope_id is not None
                ):
                    raise ValueError
            if not re.fullmatch(r"CUTBACK_WEBHOOK_SECRET_[A-Z0-9_]{1,50}", entry["secret_env"]):
                raise ValueError
            result[key] = entry
        return result
    except (TypeError, ValueError, KeyError, AttributeError):
        raise ValueError("WEBHOOK_OPERATOR_CONFIGURATION_INVALID") from None


def destination_hash(entry):
    return hashlib.sha256(
        json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class PinnedBackend(SyncBackend):
    def __init__(self, host, address):
        self.host, self.address = host, address
        self.deadline = time.monotonic() + 10

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if host != self.host or port != 443 or local_address is not None:
            raise httpcore.ConnectError("WEBHOOK_DESTINATION_DENIED")
        # Only a preapproved public literal reaches connect_tcp. TLS continues to
        # verify the ORIGINAL URL hostname through httpcore's start_tls/SNI path.
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise httpcore.ConnectTimeout("WEBHOOK_TOTAL_DEADLINE")
        stream = super().connect_tcp(
            self.address, 443, timeout=min(timeout or 3, remaining), socket_options=socket_options
        )
        return DeadlineStream(stream, self.deadline)


class DeadlineStream:
    def __init__(self, stream, deadline):
        self.stream, self.deadline = stream, deadline

    def remaining(self, timeout, exception):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise exception("WEBHOOK_TOTAL_DEADLINE")
        return min(timeout or 3, remaining)

    def read(self, max_bytes, timeout=None):
        return self.stream.read(max_bytes, timeout=self.remaining(timeout, httpcore.ReadTimeout))

    def write(self, buffer, timeout=None):
        return self.stream.write(buffer, timeout=self.remaining(timeout, httpcore.WriteTimeout))

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.stream = self.stream.start_tls(
            ssl_context,
            server_hostname=server_hostname,
            timeout=self.remaining(timeout, httpcore.ConnectTimeout),
        )
        return self

    def get_extra_info(self, info):
        return self.stream.get_extra_info(info)

    def close(self):
        self.stream.close()


def deliver_http(entry, payload, identifier, timestamp, secret):
    signature = hmac.new(
        secret.encode(),
        str(timestamp).encode() + b"." + identifier.encode() + b"." + payload,
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Cutback-Metadata-Webhook/1",
        "Idempotency-Key": identifier,
        "X-Cutback-Delivery": identifier,
        "X-Cutback-Timestamp": str(timestamp),
        "X-Cutback-Signature": "sha256=" + signature,
    }
    parts = urlsplit(entry["url"])
    # Address failover is a durable subsequent attempt, never an automatic hidden
    # transport retry. Redirects and proxies are not implemented by this pool.
    backend = PinnedBackend(parts.hostname, entry["addresses"][timestamp % len(entry["addresses"])])
    started = time.monotonic()
    with httpcore.ConnectionPool(
        ssl_context=ssl.create_default_context(),
        network_backend=backend,
        max_connections=1,
        retries=0,
    ) as pool:
        with pool.stream(
            "POST",
            entry["url"],
            headers=list(headers.items()),
            content=payload,
            extensions={"timeout": {"connect": 3, "read": 3, "write": 3, "pool": 3}},
        ) as response:
            size = 0
            for chunk in response.iter_stream():
                size += len(chunk)
                if size > 65536 or time.monotonic() - started > 10:
                    raise ValueError("WEBHOOK_RESPONSE_LIMIT")
            return response.status


def queue_incident_event(session, incident, event):
    count = session.scalar(
        select(func.count())
        .select_from(NotificationDelivery)
        .where(NotificationDelivery.project_id == incident.project_id)
    )
    configs = session.scalars(
        select(NotificationConfig)
        .where(
            NotificationConfig.project_id == incident.project_id,
            NotificationConfig.enabled.is_(True),
        )
        .limit(10)
    ).all()
    for config in configs:
        if count >= 10000:
            # Internal incidents remain available. A quota is visible in delivery
            # status and audit; it must not roll back the underlying incident.
            from cutback.server.db import AuditEvent

            session.add(
                AuditEvent(
                    project_id=incident.project_id,
                    actor="online_monitor",
                    action="notification_queue_full",
                    details={"config_id": str(config.id), "event_id": str(event.id)},
                )
            )
            continue
        session.execute(
            insert(NotificationDelivery)
            .values(
                id=uuid4(),
                project_id=incident.project_id,
                config_id=config.id,
                config_revision=config.revision,
                destination_hash=config.destination_hash,
                event_id=event.id,
                state="queued",
                attempts=0,
                available_at=datetime.now(UTC),
                payload={
                    "schema": "cutback.incident.v1",
                    "project_id": str(incident.project_id),
                    "incident_id": str(incident.id),
                    "revision": incident.revision,
                    "action": event.action,
                    "state": incident.state,
                    "alert_rule_id": str(incident.alert_rule_id),
                    "window_start": incident.window_start.isoformat(),
                    "window_end": incident.window_end.isoformat(),
                },
            )
            .on_conflict_do_nothing(constraint="uq_notification_event")
        )
        count += 1


def run_notification_one(sessions, *, enabled=False, now=None, sender=None):
    if not enabled:
        return {"status": "NOTIFICATIONS_DISABLED"}
    now = now or datetime.now(UTC)
    with sessions.begin() as session:
        eligible = or_(
            (NotificationDelivery.state == "queued") & (NotificationDelivery.available_at <= now),
            (NotificationDelivery.state == "leased") & (NotificationDelivery.lease_until <= now),
        )
        candidate = session.execute(
            select(NotificationDelivery.id, NotificationDelivery.project_id)
            .where(eligible)
            .order_by(NotificationDelivery.available_at, NotificationDelivery.id)
            .limit(1)
        ).first()
        if not candidate:
            return {"status": "IDLE"}
        from cutback.server.dataset_lineage import lock_lineage

        lock_lineage(session, candidate.project_id)
        row = session.scalar(
            select(NotificationDelivery)
            .where(NotificationDelivery.id == candidate.id, eligible)
            .with_for_update(skip_locked=True)
        )
        if not row:
            return {"status": "IDLE"}
        if row.attempts >= 5:
            row.state, row.code = "dead_letter", "ATTEMPT_LIMIT_UNCERTAIN_DELIVERY"
            return {"status": "dead_letter"}
        config = session.get(NotificationConfig, row.config_id)
        try:
            entry = destinations().get(config.destination) if config and config.enabled else None
            if (
                not entry
                or str(row.project_id) not in entry["project_ids"]
                or config.revision != row.config_revision
                or destination_hash(entry) != config.destination_hash
                or config.destination_hash != row.destination_hash
            ):
                raise ValueError
            from cutback.server.runtime import load_secret

            load_secret(entry["secret_env"])
            secret = os.environ.get(entry["secret_env"], "")
            if not 32 <= len(secret) <= 4096:
                raise ValueError
        except (ValueError, OSError):
            row.state, row.code = "dead_letter", "DESTINATION_NOT_CURRENTLY_APPROVED"
            return {"status": "dead_letter"}
        row.attempts += 1
        row.state, row.lease_token, row.lease_until = "leased", uuid4(), now + timedelta(seconds=30)
        identifier, lease = row.id, row.lease_token
        payload = json.dumps(row.payload, sort_keys=True, separators=(",", ":")).encode()
    try:
        status = (sender or deliver_http)(
            entry, payload, str(identifier), int(now.timestamp()), secret
        )
        state = (
            "delivered"
            if 200 <= status < 300
            else "queued"
            if status == 429 or status >= 500
            else "dead_letter"
        )
        code = (
            "HTTP_2XX"
            if state == "delivered"
            else "REMOTE_RETRYABLE"
            if state == "queued"
            else "REMOTE_REJECTED"
        )
    except (
        httpcore.NetworkError,
        httpcore.TimeoutException,
        httpcore.ProtocolError,
        ValueError,
        OSError,
    ):
        state, code = "queued", "NETWORK_OR_RESPONSE_UNCERTAIN"
    with sessions.begin() as session:
        row = session.get(NotificationDelivery, identifier, with_for_update=True)
        if not row or row.state != "leased" or row.lease_token != lease:
            return {"status": "STALE_LEASE"}
        row.state = "dead_letter" if state == "queued" and row.attempts >= 5 else state
        row.code, row.lease_until, row.lease_token = code, None, None
        row.available_at = now + timedelta(seconds=min(3600, 30 * 2**row.attempts))
        return {"status": row.state, "delivery_id": str(row.id), "attempts": row.attempts}
