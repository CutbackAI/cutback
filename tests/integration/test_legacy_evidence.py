from uuid import UUID

import pytest
from support_evaluation import SyntheticExecutor, authorized_dataset

from cutback.server.db import RecommendationRecord
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("version", [None, "deterministic-v2"])
def test_historical_evidence_cannot_authorize_new_changes(
    api, tenant, tmp_path, monkeypatch, version
):
    client, admin, app = api
    base, store, _, plan = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    client.post(
        base + f"/experiments/plans/{plan['id']}/approve",
        headers=admin,
        json={"plan_hash": plan["plan_hash"], "approval_ref": "synthetic-legacy-check"},
    )
    client.post(base + "/experiments", headers=admin, json={"plan_id": plan["id"]})
    assert (
        run_one(app.state.sessions, store, executor=SyntheticExecutor(), execution_enabled=True)[
            "status"
        ]
        == "COMPLETED"
    )
    rec = client.get(base + "/recommendations", headers=admin).json()[0]
    assert rec["evidence"]["evaluator_version"] == "deterministic-v3"
    with app.state.sessions.begin() as session:
        row = session.get(RecommendationRecord, UUID(rec["id"]))
        old = dict(row.evidence)
        old.pop("evaluator_version")
        if version:
            old["evaluator_version"] = version
        row.evidence = old
    review = client.post(
        base + f"/recommendations/{rec['id']}/review",
        headers=admin,
        json={"decision": "accepted_for_staging", "reason": "synthetic-legacy-check"},
    )
    assert review.status_code == 409 and review.json()["code"] == "EVALUATOR_REAPPROVAL_REQUIRED"
    with app.state.sessions.begin() as session:
        session.get(RecommendationRecord, UUID(rec["id"])).status = "accepted_for_staging"
    assert (
        client.post(
            base + f"/recommendations/{rec['id']}/patches",
            headers=admin,
            json={"approval_ref": "synthetic-legacy-check", "artifact": {}},
        ).status_code
        == 409
    )
    assert (
        client.post(
            base + "/rollouts",
            headers=admin,
            json={
                "recommendation_id": rec["id"],
                "environment": "staging",
                "baseline_ref": "synthetic-base",
                "rollback_ref": "synthetic-rollback",
                "guardrails": {"minimum_business_quality": 0.9, "min_observations": 10},
            },
        ).status_code
        == 409
    )
