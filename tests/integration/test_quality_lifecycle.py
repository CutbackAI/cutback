"""Real PostgreSQL privacy, retention and independent-confirmation boundaries."""

import runpy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from support_evaluation import dataset_body, plan_for
from test_online_quality import captured, create_quality, quality_rows
from test_online_quality import quality_setup as quality_setup

from cutback.server.dataset_lineage import expose
from cutback.server.db import DatasetRecord, ExperimentRunRecord
from cutback.server.operations import maintain
from cutback.server.quality_db import QualityEvaluation, QualityRule
from cutback.server.quality_worker import claim_quality, run_quality_one
from cutback.server.worker import load_dataset

pytestmark = pytest.mark.postgres


def test_full_quality_quota_does_not_block_unmatched_metadata(
    api, tenant, quality_setup, monkeypatch
):
    from test_monitoring import span_body

    from cutback.server.monitoring import queue_online_intake
    from cutback.server.monitoring_db import OnlineIntake

    create_quality(api, quality_setup)
    captured(api, tenant, quality_setup)
    monkeypatch.setattr("cutback.server.quality_service.MAX_QUALITY_RECEIPTS", 1)
    response = api[0].post(
        "/v1/ingest/spans",
        headers=tenant[1],
        json={"spans": [{**span_body(), "workflow": "synthetic-unmatched-workflow"}]},
    )
    assert response.status_code == 200, response.text
    result = queue_online_intake(
        api[2].state.sessions, enabled=True, only_project_id=UUID(tenant[0])
    )
    assert result["status"] == "TARGETS_QUEUED"
    with api[2].state.sessions() as session:
        assert (
            session.scalar(
                select(OnlineIntake.id).where(OnlineIntake.project_id == UUID(tenant[0]))
            )
            is None
        )


def test_independent_quality_sample_uses_real_http_and_no_inference(api, tenant, quality_setup):
    server = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "end_to_end/test_pipeline.py")
    )["server"]

    sample = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "examples/online-quality/sample.py")
    )
    with server(api[2]) as endpoint:
        result = sample["demonstrate"](
            endpoint,
            tenant[0],
            api[1]["Authorization"].removeprefix("Bearer "),
            tenant[1]["Authorization"].removeprefix("Bearer "),
        )
    assert result["provider_calls"] == 0
    from cutback.server.monitoring import queue_online_intake

    queue_online_intake(api[2].state.sessions, enabled=True, only_project_id=UUID(tenant[0]))
    for _ in range(2):
        run_quality_one(api[2].state.sessions, quality_setup[2], execution_enabled=True)
    response = api[0].get(
        quality_setup[0] + f"/quality-rules/{result['rule_id']}/evaluations", headers=tenant[2]
    )
    assert sorted(r["state"] for r in response.json()["items"]) == ["failed", "passed"]


def test_active_protected_confirmation_is_not_exposed_to_online_judge(api, tenant, quality_setup):
    client, admin, app = api
    base, policy, store = quality_setup
    data = dataset_body(40).model_copy(
        update={"policy_id": policy, "workflow": "synthetic-support"}
    )
    response = client.post(base + "/datasets", headers=admin, json=data.model_dump(mode="json"))
    assert response.status_code == 201, response.text
    dataset = response.json()
    plan = plan_for(dataset, workflow="synthetic-support")
    response = client.post(
        base + "/experiments/plans",
        headers=admin,
        json={"dataset_id": dataset["id"], "specification": plan.model_dump(mode="json")},
    )
    assert response.status_code == 201, response.text
    with app.state.sessions.begin() as session:
        record = session.get(DatasetRecord, UUID(dataset["id"]))
        frozen, _ = load_dataset(session, record, store)
        text = next(r["case"]["input"] for r in frozen["items"] if r["split"] == "test")
        run = ExperimentRunRecord(
            id=uuid4(),
            project_id=UUID(tenant[0]),
            plan_id=UUID(response.json()["id"]),
            status="QUEUED",
            results={},
        )
        session.add(run)
        session.flush()
        assert expose(
            session,
            UUID(tenant[0]),
            record.id,
            "test",
            "evaluation:" + str(run.id),
            run_id=run.id,
            protected=True,
        )["fresh_test_claim"]
    rule = create_quality(api, quality_setup, kind="model_judge")
    captured(api, tenant, quality_setup, input=text)

    class Denied:
        def execute(self, *args):
            pytest.fail("Protected confirmation reached a judge")

    result = run_quality_one(app.state.sessions, store, execution_enabled=True, executor=Denied())
    assert result["status"] == "unknown"
    assert not quality_rows(api, tenant, quality_setup, rule)[0]["request_reserved"]


def test_project_deletion_waits_for_live_quality_lease(api, tenant, quality_setup):
    create_quality(api, quality_setup)
    captured(api, tenant, quality_setup)
    client, admin, app = api
    claim = claim_quality(app.state.sessions, enabled=True)
    slug = client.get(quality_setup[0], headers=admin).json()["slug"]
    response = client.delete(quality_setup[0], headers=admin, params={"confirm_slug": slug})
    assert response.status_code == 409 and "QUALITY" in response.text
    with app.state.sessions.begin() as session:
        row = session.get(QualityEvaluation, claim["id"])
        row.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    assert (
        client.delete(quality_setup[0], headers=admin, params={"confirm_slug": slug}).status_code
        == 200
    )


def test_receipt_retention_preserves_durable_totals_and_no_missing_source_reconstruction(
    api, tenant, quality_setup
):
    rule = create_quality(api, quality_setup)
    captured(api, tenant, quality_setup)
    sessions = api[2].state.sessions
    assert run_quality_one(sessions, quality_setup[2], execution_enabled=True)["status"] == "passed"
    maintain(
        sessions, project_id=UUID(tenant[0]), now=datetime.now(UTC) + timedelta(days=40), apply=True
    )
    with sessions() as session:
        assert session.get(QualityRule, UUID(rule["id"])).evaluations_used == 1
        assert (
            session.scalar(
                select(QualityEvaluation.id).where(QualityEvaluation.rule_id == UUID(rule["id"]))
            )
            is None
        )
    assert quality_rows(api, tenant, quality_setup, rule) == []
