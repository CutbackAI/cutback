"""Durable dirty windows keep delayed/backfilled scores visible after a minute rolls over."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from cutback.server.dataset_lineage import lock_lineage
from cutback.server.db import AuditEvent
from cutback.server.monitoring_db import AlertRule, AlertWindow, OnlineEvaluation


def touch_window(session, alert, occurred):
    width = alert.specification["window_seconds"]
    start = datetime.fromtimestamp(int(occurred.timestamp()) // width * width, UTC)
    row = session.scalar(
        select(AlertWindow).where(
            AlertWindow.alert_rule_id == alert.id, AlertWindow.window_start == start
        )
    )
    if row:
        row.dirty = True
        return row
    count = session.scalar(
        select(func.count())
        .select_from(AlertWindow)
        .where(AlertWindow.project_id == alert.project_id)
    )
    if count >= 50000:
        if not alert.window_queue_full:
            session.add(
                AuditEvent(
                    project_id=alert.project_id,
                    actor="online_monitor",
                    action="alert_window_queue_full",
                    details={"alert_rule_id": str(alert.id)},
                )
            )
        alert.window_queue_full = True
        return None
    alert.window_queue_full = False
    session.execute(
        insert(AlertWindow)
        .values(
            project_id=alert.project_id,
            alert_rule_id=alert.id,
            window_start=start,
            window_end=start + timedelta(seconds=width),
            dirty=True,
            summary={},
        )
        .on_conflict_do_update(constraint="uq_alert_window", set_={"dirty": True})
    )
    return session.scalar(
        select(AlertWindow).where(
            AlertWindow.alert_rule_id == alert.id, AlertWindow.window_start == start
        )
    )


def touch_online_windows(session, row):
    for alert in session.scalars(
        select(AlertRule)
        .where(AlertRule.online_rule_id == row.rule_id, AlertRule.enabled.is_(True))
        .limit(10)
    ):
        touch_window(session, alert, row.occurred_at)


def backfill_alert_windows(sessions, alert_id):
    with sessions.begin() as session:
        project = session.scalar(select(AlertRule.project_id).where(AlertRule.id == alert_id))
        if not project:
            return
        lock_lineage(session, project)
        alert = session.get(AlertRule, alert_id, with_for_update=True)
        if not alert or not alert.enabled or alert.backfill_complete:
            return
        query = select(OnlineEvaluation).where(OnlineEvaluation.rule_id == alert.online_rule_id)
        if alert.backfill_cursor:
            query = query.where(OnlineEvaluation.id > alert.backfill_cursor)
        rows = session.scalars(query.order_by(OnlineEvaluation.id).limit(100)).all()
        for row in rows:
            if touch_window(session, alert, row.occurred_at) is None:
                return
            alert.backfill_cursor = row.id
        alert.backfill_complete = len(rows) < 100
