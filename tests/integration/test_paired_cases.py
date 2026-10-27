import json
from uuid import UUID

import pytest
from support_evaluation import SyntheticExecutor, authorized_dataset, plan_for

from cutback.server.db import DatasetRecord, PayloadSample
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


def test_failure_first_pairs_and_correction_create_new_version(api, tenant, tmp_path, monkeypatch):
    client, admin, app = api
    base, store, dataset, _ = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    spec = plan_for({"immutable_hash": dataset["immutable_hash"]}, retain_case_outputs=True)
    frozen = client.post(
        base + "/experiments/plans",
        headers=admin,
        json={"dataset_id": dataset["id"], "specification": spec.model_dump(mode="json")},
    ).json()
    client.post(
        base + f"/experiments/plans/{frozen['id']}/approve",
        headers=admin,
        json={"plan_hash": frozen["plan_hash"], "approval_ref": "synthetic-case-review"},
    )
    run_id = client.post(
        base + "/experiments", headers=admin, json={"plan_id": frozen["id"]}
    ).json()["id"]
    assert (
        run_one(
            app.state.sessions,
            store,
            executor=SyntheticExecutor(wrong=True),
            execution_enabled=True,
        )["status"]
        == "COMPLETED"
    )
    response = client.post(
        base + f"/experiments/{run_id}/cases/paired?limit=2&failed_only=true&model_key=candidate",
        headers=admin,
        json={"purpose": "synthetic-case-review"},
    )
    assert response.status_code == 200, response.text
    paired = response.json()
    assert len(paired["cases"]) == 2 and paired["more_possible"]
    case = paired["cases"][0]
    assert {row["model_key"] for row in case["outputs"]} == {"baseline", "candidate"}
    assert {row["output"] for row in case["outputs"]} == {"A", "B"}
    viewer = tenant[2]
    assert (
        client.post(
            base + f"/experiments/{run_id}/cases/paired",
            headers=viewer,
            json={"purpose": "synthetic-case-review"},
        ).status_code
        == 403
    )
    correction = client.post(
        base + f"/experiments/{run_id}/cases/correct",
        headers=admin,
        json={
            "purpose": "synthetic-case-review",
            "case_id": case["case"]["id"],
            "gold": "B",
            "correction_ref": "human-reviewed-synthetic-v2",
            "all_labels_reviewed": False,
        },
    )
    assert correction.status_code == 201, correction.text
    created = correction.json()
    assert created["id"] != dataset["id"] and created["immutable_hash"] != dataset["immutable_hash"]
    assert not created["original_evidence_changed"] and created["new_experiment_approval_required"]
    with app.state.sessions() as session:
        old = session.get(DatasetRecord, UUID(dataset["id"]))
        new = session.get(DatasetRecord, UUID(created["id"]))
        assert old.immutable_hash == dataset["immutable_hash"]
        assert not new.provenance["representative"] and not new.provenance["independent_labels"]
    original = client.post(
        base + f"/experiments/{run_id}/cases/paired?limit=2",
        headers=admin,
        json={"purpose": "synthetic-case-review"},
    ).json()
    assert original["cases"][0]["case"]["gold"] == "A"
    assert "synthetic-case-review" not in json.dumps(created.get("provenance", {}))
    from sqlalchemy import select

    with app.state.sessions.begin() as session:
        rows = session.scalars(
            select(PayloadSample).where(PayloadSample.project_id == UUID(tenant[0]))
        ).all()
        for row in rows:
            if row.metadata_safe.get("run_id") == run_id:
                row.metadata_safe = {
                    key: value
                    for key, value in row.metadata_safe.items()
                    if key not in {"case_hash", "sample_failed"}
                }
    missing_index = client.post(
        base + f"/experiments/{run_id}/cases/paired?failed_only=false",
        headers=admin,
        json={"purpose": "legacy-review"},
    ).json()
    assert not missing_index["cases"] and missing_index["unindexed_legacy_outputs"] > 0
    legacy = client.post(
        base + f"/experiments/{run_id}/cases/content?legacy_only=true&limit=2",
        headers=admin,
        json={"purpose": "legacy-review"},
    )
    assert legacy.status_code == 200 and len(legacy.json()["cases"]) == 2
