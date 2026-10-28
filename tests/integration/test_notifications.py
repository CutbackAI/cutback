import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpcore
import pytest
from test_monitoring import alert, create_rule, ingest, span_body
from test_monitoring import stop_owned_rules_after_test as stop_owned_rules_after_test

from cutback.server.incidents import evaluate_alert
from cutback.server.monitoring import run_online_one
from cutback.server.notifications import run_notification_one

pytestmark = pytest.mark.postgres


@pytest.fixture
def notification_setup(api, tenant, monkeypatch):
    destination = {
        "url": "https://hooks.example.test/incidents",
        "addresses": ["8.8.8.8"],
        "secret_env": "CUTBACK_WEBHOOK_SECRET_SYNTHETIC",
        "project_ids": [tenant[0]],
    }
    monkeypatch.setenv("CUTBACK_WEBHOOK_DESTINATIONS", json.dumps({"synthetic": destination}))
    monkeypatch.setenv("CUTBACK_WEBHOOK_SECRET_SYNTHETIC", "owned-synthetic-secret-" + "x" * 32)
    client, admin, app = api
    base = f"/v1/projects/{tenant[0]}"
    body = {
        "destination": "synthetic",
        "expected_revision": 0,
        "enabled": True,
        "authorize_delivery": True,
        "approval_ref": "owned-synthetic-receiver-only",
    }
    assert client.post(base + "/notifications", headers=tenant[2], json=body).status_code == 403
    assert (
        client.post(
            base + "/notifications", headers=admin, json={**body, "authorize_delivery": False}
        ).status_code
        == 422
    )
    config = client.post(base + "/notifications", headers=admin, json=body)
    assert config.status_code == 201, config.text
    rule = create_rule(api, tenant)
    alarm = alert(api, tenant, rule)
    anchor = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=10)
    ingest(api, tenant, [span_body(anchor + timedelta(seconds=1))])
    assert run_online_one(app.state.sessions, enabled=True)["status"] == "failed"
    evaluate_alert(app.state.sessions, UUID(alarm["id"]), now=anchor + timedelta(seconds=61))
    return base, body, destination, config.json()


def test_notifications_default_off_idempotent_metadata_payload_and_safe_status(
    api, tenant, notification_setup
):
    client, admin, app = api
    base, body, destination, config = notification_setup
    sent = []

    def sender(entry, payload, identifier, timestamp, secret):
        sent.append((payload, identifier))
        return 204

    assert (
        run_notification_one(app.state.sessions, sender=sender)["status"]
        == "NOTIFICATIONS_DISABLED"
    )
    assert sent == []
    result = run_notification_one(app.state.sessions, enabled=True, sender=sender)
    assert result["status"] == "delivered" and result["attempts"] == 1
    assert run_notification_one(app.state.sessions, enabled=True, sender=sender)["status"] == "IDLE"
    payload = json.loads(sent[0][0])
    assert set(payload) == {
        "schema",
        "project_id",
        "incident_id",
        "revision",
        "action",
        "state",
        "alert_rule_id",
        "window_start",
        "window_end",
    }
    status = client.get(base + "/notification-deliveries", headers=tenant[2])
    assert status.json()["items"][0]["state"] == "delivered"
    assert (
        "secret" not in status.text
        and "lease_token" not in status.text
        and "hooks.example.test" not in status.text
    )


def test_uncertain_notification_retries_same_id_bounded_and_dead_letters(
    api, tenant, notification_setup
):
    client, admin, app = api
    base, *_ = notification_setup
    attempts = []

    def sender(entry, payload, identifier, timestamp, secret):
        attempts.append(identifier)
        raise httpcore.ReadTimeout("PRIVATE_REMOTE_DIAGNOSTIC")

    now = datetime.now(UTC)
    for n in range(5):
        result = run_notification_one(
            app.state.sessions, enabled=True, now=now + timedelta(hours=n), sender=sender
        )
    assert result["status"] == "dead_letter" and result["attempts"] == 5
    assert len(set(attempts)) == 1 and len(attempts) == 5
    assert (
        "PRIVATE_REMOTE_DIAGNOSTIC"
        not in client.get(base + "/notification-deliveries", headers=tenant[2]).text
    )


def test_destination_changes_require_fresh_approval_and_disable_cancels(
    api, tenant, notification_setup, monkeypatch
):
    client, admin, app = api
    base, body, destination, config = notification_setup
    monkeypatch.setenv(
        "CUTBACK_WEBHOOK_DESTINATIONS",
        json.dumps({"synthetic": {**destination, "url": "https://changed.example.test/"}}),
    )

    def denied(*args):
        raise AssertionError("no outbound attempt may occur")

    assert (
        run_notification_one(app.state.sessions, enabled=True, sender=denied)["status"]
        == "dead_letter"
    )
    response = client.post(
        base + "/notifications",
        headers=admin,
        json={**body, "expected_revision": 1, "enabled": False, "authorize_delivery": False},
    )
    assert response.status_code == 201, response.text
    assert response.json()["revision"] == 2


def test_project_allowlist_and_reapproval_do_not_retarget_queued_receipts(
    api, tenant, notification_setup
):
    from uuid import uuid4

    client, admin, app = api
    base, body, destination, config = notification_setup
    foreign = client.post(
        "/v1/projects", headers=admin, json={"slug": "synthetic-hook-" + uuid4().hex}
    ).json()["id"]
    foreign_base = f"/v1/projects/{foreign}"
    assert (
        client.get(foreign_base + "/notification-destinations", headers=admin).json()["identifiers"]
        == []
    )
    assert client.post(foreign_base + "/notifications", headers=admin, json=body).status_code == 403
    response = client.post(
        base + "/notifications", headers=admin, json={**body, "expected_revision": 1}
    )
    assert response.status_code == 201, response.text

    def denied(*args):
        raise AssertionError("old queued approval cannot be retargeted")

    assert (
        run_notification_one(app.state.sessions, enabled=True, sender=denied)["status"]
        == "dead_letter"
    )


def test_project_deletion_waits_for_reserved_notification_and_cascades_export(
    api, tenant, notification_setup
):
    from sqlalchemy import func, select

    from cutback.server.monitoring_db import NotificationDelivery

    client, admin, app = api
    base, body, destination, config = notification_setup
    slug = client.get(base, headers=admin).json()["slug"]
    export = client.get(base + "/export", headers=admin)
    assert '"table": "notification_configs"' in export.text
    assert '"table": "notification_deliveries"' in export.text
    assert destination["url"] not in export.text and destination["secret_env"] not in export.text

    def sender(*args):
        response = client.delete(base, headers=admin, params={"confirm_slug": slug})
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "FINISH_IN_FLIGHT_NOTIFICATION_FIRST"
        return 204

    assert (
        run_notification_one(app.state.sessions, enabled=True, sender=sender)["status"]
        == "delivered"
    )
    assert client.delete(base, headers=admin, params={"confirm_slug": slug}).status_code == 200
    with app.state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(NotificationDelivery)
                .where(NotificationDelivery.project_id == UUID(tenant[0]))
            )
            == 0
        )
