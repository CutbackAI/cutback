"""New imports can select task-aware splits; released assignments and exposure stay frozen."""

from uuid import UUID, uuid4

import pytest
from support_evaluation import authorized_dataset, dataset_body, plan_for

from cutback.evals.datasets import digest
from cutback.server.dataset_lineage import LineageViolation, expose, inspect_exposure
from cutback.server.db import DatasetRecord, ExperimentRunRecord
from cutback.server.worker import load_dataset

pytestmark = pytest.mark.postgres


def test_versioned_split_import_preserves_legacy_hashes_and_cannot_recycle_test_exposure(
    api, tenant, tmp_path, monkeypatch
):
    client, admin, app = api
    base, store, initial, _ = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    legacy = dataset_body(24).model_dump(mode="json")
    legacy["policy_id"] = initial["provenance"]["policy_id"]
    for index, case in enumerate(legacy["cases"]):
        case.update(input="Owned policy answer case " + chr(65 + index), gold=str(14 + index))
    imported = client.post(base + "/datasets", headers=admin, json=legacy)
    assert imported.status_code == 201, imported.text
    old = imported.json()
    assert "split_policy" not in old["provenance"]
    assert old["provenance"]["content_hash"] == digest(legacy)
    assert old["provenance"]["split_counts"] == {"test": 24}
    spec = plan_for(old)
    plan = client.post(
        base + "/experiments/plans",
        headers=admin,
        json={"dataset_id": old["id"], "specification": spec.model_dump(mode="json")},
    )
    assert plan.status_code == 201, plan.text
    plan = plan.json()
    assert (
        client.post(
            base + f"/experiments/plans/{plan['id']}/approve",
            headers=admin,
            json={"plan_hash": plan["plan_hash"], "approval_ref": "owned-legacy-holdout"},
        ).status_code
        == 200
    )
    # Reserve a protected held-out run through the canonical lineage boundary.
    # Ordinary test runs record exposure but do not reserve independent confirmation.
    project = UUID(tenant[0])
    with app.state.sessions.begin() as session:
        run = ExperimentRunRecord(
            id=uuid4(), project_id=project, plan_id=UUID(plan["id"]), status="QUEUED", results={}
        )
        session.add(run)
        session.flush()
        receipt = expose(
            session,
            project,
            UUID(old["id"]),
            "test",
            "evaluation:" + str(run.id),
            run_id=run.id,
            protected=True,
        )
        assert receipt["fresh_test_claim"] is True
        run_id = run.id
    # Versioning a new partition is not permission to reuse an already reserved test family.
    result = client.post(
        base + "/datasets", headers=admin, json={**legacy, "split_policy": "task-aware-v2"}
    )
    assert result.status_code == 201, result.text
    current = result.json()
    assert current["immutable_hash"] != old["immutable_hash"]
    assert set(current["provenance"]["split_counts"]) == {"train", "validation", "test"}
    assert current["provenance"]["split_policy"] == "task-aware-v2"
    with app.state.sessions() as session:
        row = session.get(DatasetRecord, UUID(old["id"]))
        loaded, _ = load_dataset(session, row, store)
        assert loaded["immutable_hash"] == old["immutable_hash"]
        assert all(item["split"] == "test" for item in loaded["items"])
    spec = plan_for(current, split="validation")
    planned = client.post(
        base + "/experiments/plans",
        headers=admin,
        json={"dataset_id": current["id"], "specification": spec.model_dump(mode="json")},
    )
    assert planned.status_code == 201, planned.text
    planned = planned.json()
    assert (
        client.post(
            base + f"/experiments/plans/{planned['id']}/approve",
            headers=admin,
            json={"plan_hash": planned["plan_hash"], "approval_ref": "owned-new-version-review"},
        ).status_code
        == 200
    )
    denied = client.post(base + "/experiments", headers=admin, json={"plan_id": planned["id"]})
    assert denied.status_code == 409, denied.text
    with app.state.sessions.begin() as session:
        session.get(ExperimentRunRecord, run_id).status = "FAILED"
    with app.state.sessions() as session:
        with pytest.raises(LineageViolation, match="PREVIOUSLY_EXPOSED"):
            inspect_exposure(session, project, UUID(current["id"]), "test", require_fresh=True)
        _, receipt = inspect_exposure(session, project, UUID(current["id"]), "test")
        assert receipt["fresh_test_claim"] is False
        assert receipt["previously_exposed_groups"] > 0
