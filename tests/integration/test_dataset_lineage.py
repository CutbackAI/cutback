from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from support_evaluation import SyntheticExecutor, authorized_dataset, dataset_body, plan_for

from cutback.server.dataset_lineage import (
    LineageViolation,
    expose,
    inspect_exposure,
    register_groups,
)
from cutback.server.db import DatasetItem, DatasetRecord, ExperimentRunRecord
from cutback.server.lineage_db import DatasetFamily
from cutback.server.worker import load_dataset, run_one

pytestmark = pytest.mark.postgres


def test_legacy_reconciliation_preserves_frozen_data_and_never_invents_freshness(
    api, tenant, tmp_path, monkeypatch
):
    from cutback.server.dataset_lineage import legacy_test_history

    client, admin, app = api
    base, store, original, plan = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    project, dataset_id = UUID(tenant[0]), UUID(original["id"])
    with app.state.sessions.begin() as session:
        dataset = session.get(DatasetRecord, dataset_id)
        content_hash = dataset.provenance["content_hash"]
        dataset.immutable_hash = content_hash
        dataset.provenance = {
            k: v
            for k, v in dataset.provenance.items()
            if k
            not in {"hash_scheme", "content_hash", "assignment_hash", "provenance_extension_keys"}
        }
        before = []
        for row in session.scalars(select(DatasetItem).where(DatasetItem.dataset_id == dataset_id)):
            row.lineage_group_id = None
            before.append((row.id, row.split, row.group_hash))
        session.add(
            ExperimentRunRecord(
                project_id=project, plan_id=UUID(plan["id"]), status="FAILED", results={}
            )
        )
    with app.state.sessions() as session:
        assert legacy_test_history(session, project)
        frozen = load_dataset(session, session.get(DatasetRecord, dataset_id), store)[0]
    endpoint = base + "/datasets/" + str(dataset_id) + "/lineage/reconcile"
    request = {"purpose": "authorized-legacy-reconciliation", "immutable_hash": content_hash}
    assert client.post(endpoint, headers=tenant[2], json=request).status_code == 403
    assert (
        client.post(
            endpoint, headers=admin, json={**request, "immutable_hash": "0" * 64}
        ).status_code
        == 409
    )
    result = client.post(endpoint, headers=admin, json=request)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "reconciled_conservatively_exposed"
    assert result.json()["fresh_test_claim"] is False
    assert client.post(endpoint, headers=admin, json=request).json()["status"] == "already_known"
    with app.state.sessions() as session:
        assert not legacy_test_history(session, project)
        restored = session.get(DatasetRecord, dataset_id)
        loaded = load_dataset(session, restored, store)[0]
        assert restored.immutable_hash == content_hash
        assert [
            {k: v for k, v in row.items() if k != "lineage_group_id"} for row in loaded["items"]
        ] == [{k: v for k, v in row.items() if k != "lineage_group_id"} for row in frozen["items"]]
        after = [
            (r.id, r.split, r.group_hash)
            for r in session.scalars(
                select(DatasetItem).where(DatasetItem.dataset_id == dataset_id)
            )
        ]
        assert sorted(before) == sorted(after)
        with pytest.raises(LineageViolation, match="PREVIOUSLY_EXPOSED"):
            inspect_exposure(session, project, dataset_id, "test", require_fresh=True)


def approve_and_queue(client, admin, base, plan):
    response = client.post(
        base + f"/experiments/plans/{plan['id']}/approve",
        headers=admin,
        json={"plan_hash": plan["plan_hash"], "approval_ref": "owned-synthetic-lineage"},
    )
    assert response.status_code == 200, response.text
    response = client.post(base + "/experiments", headers=admin, json={"plan_id": plan["id"]})
    assert response.status_code == 202, response.text
    return response.json()["id"]


def test_changed_ids_and_dataset_versions_do_not_reset_exposed_test_groups(
    api, tenant, tmp_path, monkeypatch
):
    client, admin, app = api
    project = UUID(tenant[0])
    base, store, first, plan = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    original_run = approve_and_queue(client, admin, base, plan)
    first_result = run_one(
        app.state.sessions,
        store,
        executor=SyntheticExecutor(),
        execution_enabled=True,
        only_run_id=original_run,
    )
    assert first_result["status"] == "COMPLETED"
    assert first_result["results"]["lineage"]["fresh_test_claim"] is True
    body = dataset_body(40).model_copy(
        update={
            "policy_id": first["provenance"]["policy_id"],
            "provenance_ref": "owned-reimport-new-ids",
        }
    )
    for case in body.cases:
        case.id = "renamed-" + case.id
    response = client.post(base + "/datasets", headers=admin, json=body.model_dump(mode="json"))
    assert response.status_code == 201, response.text
    second = response.json()
    assert second["immutable_hash"] != first["immutable_hash"]
    with app.state.sessions() as session:
        with pytest.raises(LineageViolation, match="TEST_FAMILIES_PREVIOUSLY_EXPOSED"):
            inspect_exposure(session, project, UUID(second["id"]), "test", require_fresh=True)
    specification = plan_for(second)
    plan2 = client.post(
        base + "/experiments/plans",
        headers=admin,
        json={"dataset_id": second["id"], "specification": specification.model_dump(mode="json")},
    ).json()
    second_run = approve_and_queue(client, admin, base, plan2)
    result = run_one(
        app.state.sessions,
        store,
        executor=SyntheticExecutor(),
        execution_enabled=True,
        only_run_id=second_run,
    )
    assert result["status"] == "COMPLETED"
    decision = result["results"]["recommendations"][0]
    assert decision["sample_quality_gate_passed"] is True
    assert decision["quality_gate_passed"] is False
    assert decision["decision_reason"] == "test_group_lineage_not_fresh"
    assert result["results"]["search_frontier"]["eligible_candidates"] == 0
    recommendation = next(
        r
        for r in client.get(base + "/recommendations", headers=admin).json()
        if r["run_id"] == second_run
    )
    assert (
        client.post(
            base + f"/recommendations/{recommendation['id']}/review",
            headers=admin,
            json={"decision": "accepted_for_staging", "reason": "must-not-reset-heldout-history"},
        ).status_code
        == 409
    )
    with app.state.sessions() as session:
        one = session.get(DatasetRecord, UUID(first["id"]))
        two = session.get(DatasetRecord, UUID(second["id"]))
        assert one.provenance["hash_scheme"] == "cutback.dataset.lineage.v1"
        a, _ = load_dataset(session, one, store)
        b, _ = load_dataset(session, two, store)
        assert {r["lineage_group_id"] for r in a["items"]} == {
            r["lineage_group_id"] for r in b["items"]
        }


def test_global_family_merges_are_transitive_and_frozen_assignments_do_not_change(
    api, tenant, tmp_path, monkeypatch
):
    from cutback.evals.datasets import Case

    client, _, app = api
    base, store, dataset, _ = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    project = UUID(tenant[0])
    # New declared customer groups connect two previously separate input families.
    with app.state.sessions.begin() as session:
        before = {
            r.id: (r.split, r.group_hash, r.lineage_group_id)
            for r in session.scalars(
                select(DatasetItem).where(DatasetItem.dataset_id == UUID(dataset["id"]))
            )
        }
        bridge = [
            Case(
                id="bridge-one",
                input=dataset_body(40).cases[0].input,
                group="synthetic-shared-customer",
            ),
            Case(
                id="bridge-two",
                input=dataset_body(40).cases[1].input,
                group="synthetic-shared-customer",
            ),
        ]
        linked = register_groups(session, project, bridge)
        assert linked["bridge-one"] == linked["bridge-two"]
    with app.state.sessions() as session:
        after = {
            r.id: (r.split, r.group_hash, r.lineage_group_id)
            for r in session.scalars(
                select(DatasetItem).where(DatasetItem.dataset_id == UUID(dataset["id"]))
            )
        }
        assert before == after
        original = session.get(DatasetRecord, UUID(dataset["id"]))
        assert (
            load_dataset(session, original, store)[0]["immutable_hash"] == dataset["immutable_hash"]
        )
        # Any selected split containing the now-linked old groups must be re-frozen.
        split = next(
            r.split
            for r in session.scalars(
                select(DatasetItem).where(DatasetItem.dataset_id == original.id)
            )
            if r.lineage_group_id in {before_id for _, _, before_id in before.values()}
            and r.lineage_group_id in {v for v in linked.values()}
        )
        with pytest.raises(LineageViolation, match="GLOBAL_GROUPING_CHANGED"):
            inspect_exposure(session, project, original.id, split)
    other = uuid4()
    with app.state.sessions() as session:
        assert not session.scalar(
            select(func.count()).select_from(DatasetFamily).where(DatasetFamily.project_id == other)
        )


def test_concurrent_independent_claims_serialize_before_any_execution(
    api, tenant, tmp_path, monkeypatch
):
    _, _, app = api
    _, _, dataset, plan = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    project, dataset_id = UUID(tenant[0]), UUID(dataset["id"])

    def claim(_):
        try:
            with app.state.sessions.begin() as session:
                run = ExperimentRunRecord(
                    id=uuid4(),
                    project_id=project,
                    plan_id=UUID(plan["id"]),
                    status="QUEUED",
                    results={},
                )
                session.add(run)
                session.flush()
                return expose(
                    session,
                    project,
                    dataset_id,
                    "test",
                    "evaluation:" + str(run.id),
                    run_id=run.id,
                    protected=True,
                )["fresh_test_claim"]
        except LineageViolation:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim, range(2))) == [False, True]
