"""Closed event-time windows, deduplicated incidents and explicit recovery."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from cutback.server.db import AuditEvent
from cutback.server.monitoring_contracts import AlertRuleInput
from cutback.server.monitoring_db import (
    AlertRule,
    AlertWindow,
    Incident,
    IncidentEvent,
    OnlineEvaluation,
)


def window_summary(session, rule, start, end):
    spec = AlertRuleInput.model_validate(rule.specification)
    counts = {
        state: 0
        for state in ("passed", "failed", "error", "unknown", "missing", "queued", "leased")
    }
    counts.update(
        dict(
            session.execute(
                select(OnlineEvaluation.state, func.count())
                .where(
                    OnlineEvaluation.rule_id == rule.online_rule_id,
                    OnlineEvaluation.occurred_at >= start,
                    OnlineEvaluation.occurred_at < end,
                )
                .group_by(OnlineEvaluation.state)
            ).all()
        )
    )
    total = sum(counts.values())
    pending = counts["queued"] + counts["leased"]
    if spec.signal == "failure_rate":
        denominator, numerator = counts["passed"] + counts["failed"], counts["failed"]
    else:
        denominator = total - pending
        numerator = counts["error"] if spec.signal == "evaluator_error_rate" else counts["missing"]
    rate = Decimal(numerator) / Decimal(denominator) if denominator else None
    if denominator < spec.min_samples:
        status = "no_data" if not total else "insufficient_data"
    elif rate is not None and rate >= spec.threshold:
        status = "breaching"
    elif (
        pending
        or counts["unknown"]
        or (spec.signal == "failure_rate" and (counts["missing"] or counts["error"]))
    ):
        status = "incomplete"
    else:
        status = "healthy_observed_sample"
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "status": status,
        "signal": spec.signal,
        "counts": counts,
        "denominator": denominator,
        "rate": str(rate) if rate is not None else None,
        "population_coverage": "unknown",
        "retention_coverage": "unknown",
        "window_semantics": "event_time_half_open_retained_sample",
    }


def incident_event(session, incident, actor, action):
    event = IncidentEvent(
        project_id=incident.project_id,
        incident_id=incident.id,
        revision=incident.revision,
        actor=actor,
        action=action,
        owner=incident.owner,
    )
    session.add(event)
    session.flush()
    from cutback.server.notifications import queue_incident_event

    queue_incident_event(session, incident, event)
    session.add(
        AuditEvent(
            project_id=incident.project_id,
            actor=actor,
            action="incident_" + action,
            details={"incident_id": str(incident.id), "revision": incident.revision},
        )
    )


def evaluate_alert(sessions, rule_id, *, now=None, window_end=None):
    now = now or datetime.now(UTC)
    with sessions.begin() as session:
        from cutback.server.dataset_lineage import lock_lineage

        project = session.scalar(select(AlertRule.project_id).where(AlertRule.id == rule_id))
        if not project:
            return {"status": "DISABLED"}
        lock_lineage(session, project)
        rule = session.get(AlertRule, rule_id, with_for_update=True)
        if not rule or not rule.enabled:
            return {"status": "DISABLED"}
        spec = AlertRuleInput.model_validate(rule.specification)
        rule.last_checked_at = now
        end_seconds = (
            (int(now.timestamp()) - spec.grace_seconds) // spec.window_seconds * spec.window_seconds
        )
        end = window_end or datetime.fromtimestamp(end_seconds, UTC)
        if end > datetime.fromtimestamp(end_seconds, UTC):
            return {"status": "WINDOW_NOT_CLOSED"}
        start = end - timedelta(seconds=spec.window_seconds)
        summary = window_summary(session, rule, start, end)
        if rule.latest_window_end is None or end >= rule.latest_window_end:
            rule.latest_window_end, rule.latest_window = end, summary
        from cutback.server.alert_windows import touch_window

        window = touch_window(session, rule, start)
        if window:
            window.dirty, window.summary = False, summary
        active = session.scalar(
            select(Incident)
            .where(
                Incident.alert_rule_id == rule.id,
                Incident.state.in_(["open", "acknowledged"]),
            )
            .order_by(Incident.created_at)
            .limit(1)
            .with_for_update()
        )
        if summary["status"] == "breaching" and not active:
            existing = session.scalar(
                select(Incident.id).where(
                    Incident.alert_rule_id == rule.id, Incident.window_start == start
                )
            )
            if not existing and (
                rule.last_opened_at is None
                or start >= rule.last_opened_at + timedelta(seconds=spec.cooldown_seconds)
            ):
                incident = Incident(
                    project_id=rule.project_id,
                    alert_rule_id=rule.id,
                    window_start=start,
                    window_end=end,
                    evidence=summary,
                )
                session.add(incident)
                session.flush()
                rule.last_opened_at = start
                incident_event(session, incident, "online_monitor", "opened")
        elif (
            summary["status"] == "healthy_observed_sample" and active and start >= active.window_end
        ):
            active.state, active.revision = "resolved", active.revision + 1
            incident_event(session, active, "online_monitor", "recovered")
        return summary


def evaluate_alerts(sessions, *, enabled=False, now=None):
    if not enabled:
        return {"status": "ALERT_EXECUTION_DISABLED"}
    # Ordered, bounded pagination: revisit every enabled rule over worker cycles.
    with sessions() as session:
        ids = list(
            session.scalars(
                select(AlertRule.id)
                .where(AlertRule.enabled.is_(True))
                .order_by(AlertRule.last_checked_at, AlertRule.id)
                .limit(50)
            )
        )
    failures = []
    for rule in ids:
        try:
            from cutback.server.alert_windows import backfill_alert_windows

            backfill_alert_windows(sessions, rule)
            with sessions() as session:
                record = session.get(AlertRule, rule)
                now_at = now or datetime.now(UTC)
                ends = (
                    list(
                        session.scalars(
                            select(AlertWindow.window_end)
                            .where(
                                AlertWindow.alert_rule_id == rule,
                                AlertWindow.dirty.is_(True),
                                AlertWindow.window_end
                                <= now_at
                                - timedelta(seconds=record.specification["grace_seconds"]),
                            )
                            .order_by(AlertWindow.window_end)
                            .limit(5)
                        )
                    )
                    if record
                    else []
                )
            for end in ends:
                evaluate_alert(sessions, rule, now=now, window_end=end)
            evaluate_alert(sessions, rule, now=now)
        except Exception:
            with sessions.begin() as session:
                record = session.get(AlertRule, rule, with_for_update=True)
                if record:
                    record.last_checked_at = now or datetime.now(UTC)
                    session.add(
                        AuditEvent(
                            project_id=record.project_id,
                            actor="online_monitor",
                            action="alert_evaluation_failed",
                            details={"rule_id": str(rule), "code": "ALERT_EVALUATION_FAILED"},
                        )
                    )
            failures.append({"rule_id": str(rule), "code": "ALERT_EVALUATION_FAILED"})
    return {"status": "EVALUATED", "rules": len(ids), "failures": failures}
