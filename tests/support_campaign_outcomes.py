"""Later independently declared synthetic traffic after actual campaign/application checks."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from test_task_outcomes import declaration, environment, ingest, spans

from cutback.campaign_outcomes import record_outcomes
from cutback.server.economics_db import ImprovementObservation


def assert_campaign_outcome_link(api, tenant, root, control, artifact, workflow):
    base = f"/v1/projects/{tenant[0]}"
    rec = artifact["recommendation_id"]
    accepted = api[0].post(
        base + f"/recommendations/{rec}/review",
        headers=api[1],
        json={"decision": "accepted_for_staging", "reason": "owned-synthetic-review-only"},
    )
    assert accepted.status_code == 200, accepted.text
    uploaded = api[0].post(
        base + f"/recommendations/{rec}/patches",
        headers=api[1],
        json={"approval_ref": "owned-synthetic-patch", "artifact": artifact},
    )
    assert uploaded.status_code == 201, uploaded.text
    for outcome in ("success", "failure"):
        item = declaration(
            workflow=workflow,
            release="synthetic-later-release",
            outcome=outcome,
            components=[{"key": "synthetic-compute", "kind": "compute", "amount_usd": "0.25"}],
        )
        spans(api, tenant, item)
        assert ingest(api, tenant, item).status_code == 200
    frozen = api[0].post(
        base + "/task-cost-snapshots",
        headers=api[1],
        params={"environment_id": environment(api, tenant[0])},
        json={
            "snapshot_id": str(uuid4()),
            "start": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "end": datetime.now(UTC).isoformat(),
            "workflow": workflow,
            "release": "synthetic-later-release",
            "expected_tasks": 2,
        },
    )
    assert frozen.status_code == 200, frozen.text
    arguments = dict(
        candidate_snapshot_id=frozen.json()["id"],
        change_id=uploaded.json()["id"],
        release_linkage_attested=True,
        assumptions={
            "all_optimization_runs_declared_complete": True,
            "other_optimization_costs_declared_complete": True,
            "min_tasks_per_window": 1,
            "forecast_successful_tasks": 100,
        },
    )

    class LostAcknowledgement:
        endpoint = control.endpoint
        lost = False

        def __call__(self, method, path, body=None):
            assert not path.endswith("/content")
            result = control(method, path, body)
            if method == "POST" and path.endswith("/improvement-observations") and not self.lost:
                self.lost = True
                raise ValueError("OWNED_RESPONSE_ACK_LOST")
            return result

    intermittent = LostAcknowledgement()
    with pytest.raises(ValueError, match="ACK_LOST"):
        record_outcomes(root, intermittent, **arguments)
    result = record_outcomes(root, intermittent, **arguments)
    assert result["next_action"] == "REVIEW_ROLLBACK"
    assert not result["traffic_changed"] and not result["action_authorized"]
    assert result["provider_calls"] == 0
    evidence = result["receipt"]["evidence"]
    assert evidence["source_diff_sha256"] == artifact["diff_sha256"]
    assert evidence["report"]["descriptive_outcome_non_regression"] is False
    assert evidence["report"]["declared_optimization_total_usd"] is not None
    assert len(evidence["run_costs"]) == 3 and evidence["missing_predecessor_costs"] == 0
    assert evidence["report"]["production_savings_usd"] is None
    assert evidence["report"]["causal_attribution"] == "not_established"
    assert record_outcomes(root, control, **arguments) == result
    with api[2].state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ImprovementObservation)
                .where(ImprovementObservation.project_id == UUID(tenant[0]))
            )
            == 1
        )
    saved = "".join(
        p.read_text(encoding="utf-8")
        for p in (root / ".cutback/agent/campaign-observations").glob("*.json")
    )
    assert control.token not in saved and "Return A only." not in saved
    unknown = record_outcomes(root, control, **{**arguments, "assumptions": {}})
    assert unknown["receipt"]["evidence"]["report"]["declared_optimization_total_usd"] is None
