from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, update

from cutback.server.incidents import evaluate_alert, evaluate_alerts
from cutback.server.monitoring import (
    backfill_one,
    claim_online,
    epoch_ns,
    finish_online,
    queue_online_intake,
    run_online_one,
)
from cutback.server.monitoring_db import (
    AlertRule,
    Incident,
    OnlineEvaluation,
    OnlineIntake,
    OnlineRule,
)
from cutback.server.trace_db import ExecutionSpan

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def stop_owned_rules_after_test(api, tenant):
    yield
    with api[2].state.sessions.begin() as session:
        for cls in (OnlineRule, AlertRule):
            session.execute(
                update(cls).where(cls.project_id == UUID(tenant[0])).values(enabled=False)
            )


def rule_body(now=None, **extra):
    now = now or datetime.now(UTC)
    return dict(
        version_id=str(uuid4()),
        key="execution",
        expected_revision=0,
        environment="staging",
        filter={"workflow": "synthetic-support"},
        evaluator={"kind": "execution_ok"},
        starts_at=(now - timedelta(hours=1)).isoformat(),
        expires_at=(now + timedelta(days=1)).isoformat(),
        max_evaluations=100,
        approval_ref="owned-synthetic-online-rule",
        **extra,
    )


def create_rule(api, tenant, body=None):
    client, admin, _ = api
    result = client.post(
        f"/v1/projects/{tenant[0]}/online-rules", headers=admin, json=body or rule_body()
    )
    assert result.status_code == 201, result.text
    return result.json()


def span_body(at=None, status="error", **extra):
    started = epoch_ns(at or datetime.now(UTC) - timedelta(minutes=10))
    return dict(
        trace_id=uuid4().hex,
        span_id=uuid4().hex[:16],
        operation="synthetic-reply",
        service="synthetic-desk",
        workflow="synthetic-support",
        kind="chain",
        status=status,
        started_ns=started,
        ended_ns=started + 1000000,
        **extra,
    )


def ingest(api, tenant, spans):
    response = api[0].post("/v1/ingest/spans", headers=tenant[1], json={"spans": spans})
    assert response.status_code == 200, response.text
    # Exercise the distinct durable intake worker; ingestion itself must not
    # fan out rules or evaluate in the request path.
    for _ in range(11):
        outcome = queue_online_intake(
            api[2].state.sessions, enabled=True, only_project_id=UUID(tenant[0])
        )
        if outcome["status"] != "TARGETS_QUEUED":
            break
    return response.json()


def jobs(api, tenant, rule):
    response = api[0].get(
        f"/v1/projects/{tenant[0]}/online-rules/{rule['id']}/evaluations", headers=tenant[2]
    )
    assert response.status_code == 200, response.text
    assert "lease_token" not in response.text
    return response.json()["items"]


def test_off_path_idempotence_scope_revision_and_disabled_default(api, tenant):
    rule = create_rule(api, tenant)
    client, admin, app = api
    base = f"/v1/projects/{tenant[0]}"
    assert (
        client.post(base + "/online-rules", headers=tenant[2], json=rule_body()).status_code == 403
    )
    span = span_body()
    response = client.post("/v1/ingest/spans", headers=tenant[1], json={"spans": [span, span]})
    assert response.status_code == 200 and response.json()["accepted"] == 1
    assert jobs(api, tenant, rule) == []
    queue_online_intake(app.state.sessions, enabled=True, only_project_id=UUID(tenant[0]))
    assert len(jobs(api, tenant, rule)) == 1
    assert run_online_one(app.state.sessions)["status"] == "ONLINE_EXECUTION_DISABLED"
    assert jobs(api, tenant, rule)[0]["state"] == "queued"
    result = run_online_one(app.state.sessions, enabled=True)
    assert result["status"] == "failed"
    result = jobs(api, tenant, rule)[0]
    assert result["result"]["cost_usd"] == "0"
    assert "not_task_success" in result["result"]["evidence"]
    body = dict(rule["specification"], version_id=str(uuid4()), expected_revision=1)
    next_rule = create_rule(api, tenant, body)
    assert next_rule["revision"] == 2
    assert (
        client.post(
            base + f"/online-rules/{rule['id']}/switch",
            headers=admin,
            json={"expected_revision": 1, "expected_enabled": False, "enabled": True},
        ).status_code
        == 409
    )
    assert (
        client.post(base + "/online-rules", headers=admin, json=body).json()["id"]
        == next_rule["id"]
    )
    foreign = client.post(
        "/v1/projects", headers=admin, json={"slug": "foreign-" + uuid4().hex}
    ).json()
    assert (
        client.get(
            f"/v1/projects/{foreign['id']}/online-rules/{rule['id']}/evaluations", headers=admin
        ).status_code
        == 404
    )
    assert (
        client.get(f"/v1/projects/{foreign['id']}/online-rules", headers=tenant[2]).status_code
        == 403
    )


def test_backfill_native_late_arrivals_and_filter(api, tenant):
    before = span_body()
    ignored = dict(span_body(), workflow="different")
    ingest(api, tenant, [before, ignored])
    rule = create_rule(api, tenant)
    sessions = api[2].state.sessions
    assert jobs(api, tenant, rule) == []
    for _ in range(100):
        backfill_one(sessions, enabled=True)
        owned = jobs(api, tenant, rule)
        if owned:
            break
    assert len(owned) == 1
    ingest(api, tenant, [span_body()])
    assert len(jobs(api, tenant, rule)) == 2


def test_durable_nonbillable_lease_recovery_and_exact_budget(api, tenant):
    rule = create_rule(api, tenant, dict(rule_body(), max_evaluations=1))
    ingest(api, tenant, [span_body(), span_body()])
    sessions = api[2].state.sessions
    now = datetime.now(UTC)
    first = claim_online(sessions, enabled=True, now=now)
    assert first and claim_online(sessions, enabled=True, now=now + timedelta(seconds=1)) is None
    # Second queued target consumes the remaining budget state; it is never called.
    recovered = claim_online(sessions, enabled=True, now=now + timedelta(seconds=31))
    assert recovered and recovered[0] == first[0] and recovered[1] != first[1]
    assert (
        finish_online(sessions, first, now=now + timedelta(seconds=31))["status"] == "STALE_LEASE"
    )
    assert finish_online(sessions, recovered, now=now + timedelta(seconds=31))["status"] == "failed"
    rows = jobs(api, tenant, rule)
    assert sorted(r["state"] for r in rows) == ["failed", "unknown"]
    with sessions() as session:
        assert session.get(OnlineRule, UUID(rule["id"])).evaluations_used == 1


def test_concurrent_claim_cannot_repeat_target_or_budget(api, tenant):
    rule = create_rule(api, tenant)
    ingest(api, tenant, [span_body()])
    sessions = api[2].state.sessions
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: claim_online(sessions, enabled=True), range(4)))
    actual = [claim for claim in claims if claim]
    assert len(actual) == 1
    finish_online(sessions, actual[0])
    with sessions() as session:
        assert session.get(OnlineRule, UUID(rule["id"])).evaluations_used == 1


def external_result(span, **extra):
    return dict(
        result_id=str(uuid4()),
        source="synthetic-ci",
        evaluator="priority",
        evaluator_version="v1",
        trace_id=span["trace_id"],
        span_id=span["span_id"],
        score_type="boolean",
        score=False,
        outcome="failed",
        observed_at=datetime.now(UTC).isoformat(),
        **extra,
    )


def test_external_results_wait_missing_error_and_ambiguity_remain_separate(api, tenant):
    spec = dict(
        rule_body(),
        evaluator={
            "kind": "external_boolean",
            "source": "synthetic-ci",
            "evaluator": "priority",
            "evaluator_version": "v1",
        },
        wait_seconds=30,
    )
    rule = create_rule(api, tenant, spec)
    spans = [span_body() for _ in range(4)]
    ingest(api, tenant, spans)
    sessions = api[2].state.sessions
    now = datetime.now(UTC)
    for _ in spans:
        run_online_one(sessions, enabled=True, now=now)
    assert {r["state"] for r in jobs(api, tenant, rule)} == {"queued"}
    results = [
        external_result(spans[0]),
        dict(
            external_result(spans[1]),
            outcome="error",
            score_type="none",
            score=None,
            error_code="SYNTHETIC_OUTAGE",
        ),
        external_result(spans[2]),
        external_result(spans[2]),
    ]
    response = api[0].post("/v1/ingest/evaluations", headers=tenant[1], json={"results": results})
    assert response.status_code == 200, response.text
    for _ in spans:
        run_online_one(sessions, enabled=True, now=now + timedelta(seconds=31))
    assert sorted(r["state"] for r in jobs(api, tenant, rule)) == [
        "error",
        "failed",
        "missing",
        "unknown",
    ]


def test_source_expiry_is_unknown_not_failure_or_success(api, tenant):
    rule = create_rule(api, tenant)
    ingest(api, tenant, [span_body()])
    with api[2].state.sessions.begin() as session:
        session.execute(delete(ExecutionSpan).where(ExecutionSpan.project_id == UUID(tenant[0])))
    assert run_online_one(api[2].state.sessions, enabled=True)["status"] == "unknown"
    assert jobs(api, tenant, rule)[0]["result"]["code"] == "SOURCE_EXPIRED"


def alert(api, tenant, rule, **extra):
    body = dict(
        version_id=str(uuid4()),
        key="quality",
        expected_revision=0,
        online_rule_id=rule["id"],
        window_seconds=60,
        grace_seconds=0,
        min_samples=1,
        threshold="0.5",
        cooldown_seconds=0,
        **extra,
    )
    response = api[0].post(f"/v1/projects/{tenant[0]}/alert-rules", headers=api[1], json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_incident_dedup_cas_owner_nodata_and_observed_recovery(api, tenant):
    rule = create_rule(api, tenant)
    alarm = alert(api, tenant, rule)
    sessions = api[2].state.sessions
    anchor = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=10)
    ingest(api, tenant, [span_body(anchor + timedelta(seconds=1))])
    run_online_one(sessions, enabled=True)
    when = anchor + timedelta(seconds=61)
    assert evaluate_alert(sessions, UUID(alarm["id"]), now=when)["status"] == "breaching"
    evaluate_alert(sessions, UUID(alarm["id"]), now=when)
    path = f"/v1/projects/{tenant[0]}/incidents"
    rows = api[0].get(path, headers=tenant[2]).json()["items"]
    assert len(rows) == 1
    row = rows[0]
    assert row["evidence"]["population_coverage"] == "unknown"
    transition = path + "/" + row["id"] + "/transition"
    assert (
        api[0]
        .post(transition, headers=tenant[2], json={"expected_revision": 1, "action": "acknowledge"})
        .status_code
        == 403
    )
    assert (
        api[0]
        .post(
            transition,
            headers=api[1],
            json={"expected_revision": 1, "action": "assign", "owner": "synthetic-oncall"},
        )
        .json()["revision"]
        == 2
    )
    assert (
        api[0]
        .post(transition, headers=api[1], json={"expected_revision": 1, "action": "resolve"})
        .status_code
        == 409
    )
    assert (
        api[0]
        .post(transition, headers=api[1], json={"expected_revision": 2, "action": "acknowledge"})
        .json()["state"]
        == "acknowledged"
    )
    assert (
        evaluate_alert(sessions, UUID(alarm["id"]), now=when + timedelta(minutes=1))["status"]
        == "no_data"
    )
    assert api[0].get(path, headers=tenant[2]).json()["items"][0]["state"] == "acknowledged"
    ingest(api, tenant, [span_body(anchor + timedelta(seconds=60), status="ok")])
    run_online_one(sessions, enabled=True)
    assert (
        evaluate_alert(sessions, UUID(alarm["id"]), now=when + timedelta(minutes=1))["status"]
        == "healthy_observed_sample"
    )
    detail = api[0].get(path + "/" + row["id"], headers=tenant[2]).json()
    assert detail["incident"]["state"] == "resolved"
    assert [e["action"] for e in detail["events"]] == [
        "opened",
        "assign",
        "acknowledge",
        "recovered",
    ]


def test_saved_views_are_typed_shared_cas_and_exported_deleted(api, tenant):
    client, admin, app = api
    base = f"/v1/projects/{tenant[0]}"
    body = dict(
        view_id=str(uuid4()),
        key="synthetic-quality",
        expected_revision=0,
        environment="staging",
        page="incidents",
        incident_state="open",
        filter={"workflow": "synthetic-support"},
    )
    assert client.post(base + "/saved-views", headers=tenant[2], json=body).status_code == 403
    assert (
        client.post(
            base + "/saved-views", headers=admin, json={**body, "token": "DO_NOT_ECHO"}
        ).status_code
        == 422
    )
    response = client.post(base + "/saved-views", headers=admin, json=body)
    assert response.status_code == 201, response.text
    assert client.get(base + "/saved-views", headers=tenant[2]).json()["items"][0]["revision"] == 1
    assert (
        client.post(
            base + "/saved-views", headers=admin, json={**body, "page": "traces"}
        ).status_code
        == 409
    )
    create_rule(api, tenant)
    ingest(api, tenant, [span_body()])
    export = client.get(base + "/export", headers=admin)
    assert '"table": "saved_views"' in export.text and '"table": "online_rules"' in export.text
    assert "DO_NOT_ECHO" not in export.text
    project = client.get(base, headers=admin).json()
    response = client.delete(base, headers=admin, params={"confirm_slug": project["slug"]})
    assert response.status_code == 200, response.text
    with app.state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(OnlineRule)
                .where(OnlineRule.project_id == UUID(tenant[0]))
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(Incident)
                .where(Incident.project_id == UUID(tenant[0]))
            )
            == 0
        )


def test_independent_monitoring_example_uses_real_rules_and_worker(api, tenant):
    import runpy
    from pathlib import Path

    import httpx

    sample = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "examples/online-monitoring/sample.py")
    )
    client, admin, app = api

    def forward(request):
        response = client.request(
            request.method,
            request.url.raw_path.decode(),
            headers=dict(request.headers),
            content=request.content,
        )
        return httpx.Response(
            response.status_code, content=response.content, headers=response.headers
        )

    result = sample["demonstrate"](
        "https://cutback.example",
        tenant[0],
        admin["Authorization"][7:],
        tenant[1]["Authorization"][7:],
        transport=httpx.MockTransport(forward),
    )
    assert result["accepted"] == 1 and result["provider_calls"] == 0
    queue_online_intake(app.state.sessions, enabled=True, only_project_id=UUID(tenant[0]))
    assert run_online_one(app.state.sessions, enabled=True)["status"] == "failed"
    assert evaluate_alert(app.state.sessions, UUID(result["alert_id"]))["status"] == "breaching"


def test_late_score_revisits_old_window_without_duplicate_incident(api, tenant):
    now = datetime.now(UTC)
    rule = create_rule(
        api,
        tenant,
        dict(
            rule_body(),
            evaluator={
                "kind": "external_boolean",
                "source": "synthetic-ci",
                "evaluator": "priority",
                "evaluator_version": "v1",
            },
            wait_seconds=300,
        ),
    )
    alarm = alert(api, tenant, rule)
    span = span_body()
    ingest(api, tenant, [span])
    sessions = api[2].state.sessions
    run_online_one(sessions, enabled=True, now=now + timedelta(seconds=1))
    evaluate_alerts(sessions, enabled=True, now=now + timedelta(seconds=1))
    base = f"/v1/projects/{tenant[0]}"
    assert api[0].get(base + "/incidents", headers=tenant[2]).json()["items"] == []
    response = api[0].post(
        "/v1/ingest/evaluations", headers=tenant[1], json={"results": [external_result(span)]}
    )
    assert response.status_code == 200, response.text
    run_online_one(sessions, enabled=True, now=now + timedelta(seconds=17))
    for _ in range(2):
        assert not evaluate_alerts(sessions, enabled=True, now=now + timedelta(seconds=17))[
            "failures"
        ]
    incidents = api[0].get(base + "/incidents", headers=tenant[2]).json()["items"]
    assert len(incidents) == 1 and incidents[0]["state"] == "open"
    windows = (
        api[0].get(base + f"/alert-rules/{alarm['id']}/windows", headers=tenant[2]).json()["items"]
    )
    assert any(w["summary"].get("status") == "breaching" and not w["dirty"] for w in windows)


def test_retention_never_turns_active_incident_into_healthy(api, tenant):
    from cutback.server.operations import maintain

    rule = create_rule(api, tenant)
    alert(api, tenant, rule)
    now = datetime.now(UTC)
    ingest(api, tenant, [span_body()])
    sessions = api[2].state.sessions
    run_online_one(sessions, enabled=True)
    evaluate_alerts(sessions, enabled=True)
    base = f"/v1/projects/{tenant[0]}"
    assert len(api[0].get(base + "/incidents", headers=tenant[2]).json()["items"]) == 1
    maintain(
        sessions,
        project_id=UUID(tenant[0]),
        now=now + timedelta(days=2),
        raw_days=1,
        aggregate_days=30,
        apply=True,
    )
    evaluate_alerts(sessions, enabled=True, now=now + timedelta(days=2))
    assert api[0].get(base + "/incidents", headers=tenant[2]).json()["items"][0]["state"] == "open"
    assert jobs(api, tenant, rule) == []


def test_intake_outbox_is_one_per_span_not_one_per_rule_and_drains_in_worker(api, tenant):
    for i in range(10):
        create_rule(api, tenant, dict(rule_body(), key=f"synthetic-outbox-{i}"))
    spans = [span_body() for _ in range(25)]
    client, admin, app = api
    response = client.post("/v1/ingest/spans", headers=tenant[1], json={"spans": spans})
    assert response.status_code == 200, response.text
    project = UUID(tenant[0])
    with app.state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(OnlineIntake)
                .where(OnlineIntake.project_id == project)
            )
            == 25
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(OnlineEvaluation)
                .where(OnlineEvaluation.project_id == project)
            )
            == 0
        )
    result = queue_online_intake(app.state.sessions, enabled=True, only_project_id=project)
    assert result["spans_processed"] == 20
    with app.state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(OnlineIntake)
                .where(OnlineIntake.project_id == project)
            )
            == 5
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(OnlineEvaluation)
                .where(OnlineEvaluation.project_id == project)
            )
            == 200
        )
    assert (
        client.post("/v1/ingest/spans", headers=tenant[1], json={"spans": spans}).json()[
            "duplicates"
        ]
        == 25
    )
    depth = client.get(f"/v1/projects/{tenant[0]}/online-rules", headers=tenant[2]).json()[
        "pending_intake"
    ]
    assert depth == 5
