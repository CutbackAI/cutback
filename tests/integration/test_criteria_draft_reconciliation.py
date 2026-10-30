"""A lost nonbillable preparation reply cannot duplicate encrypted inputs or authority."""

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from test_reviewed_criteria_workflow import prepared

from cutback.server.criteria_db import CriteriaVersionRecord
from cutback.server.db import PayloadSample

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("kind", ["criteria", "plan"])
def test_client_identity_collision_across_projects_is_scoped_and_atomic(
    api, tenant, tmp_path, monkeypatch, kind
):
    from support_evaluation import authorized_dataset, plan_for

    from cutback.server.db import DatasetRecord, ExperimentPlanRecord

    base, store, generation = prepared(api, tenant, tmp_path, monkeypatch)
    other = (
        api[0]
        .post("/v1/projects", headers=api[1], json={"slug": "owned-collision-" + uuid4().hex})
        .json()["id"]
    )
    other_base, _, other_dataset, _ = authorized_dataset(
        api, (other, None, None), tmp_path, monkeypatch, store=store
    )
    identity = str(uuid4())
    if kind == "criteria":
        route, model = "/criteria/drafts", CriteriaVersionRecord
        bodies = [
            {**generation, "request_id": identity},
            {**generation, "request_id": identity, "dataset_id": other_dataset["id"]},
        ]
    else:
        route, model = "/experiments/plans", ExperimentPlanRecord
        with api[2].state.sessions() as session:
            first = session.get(DatasetRecord, UUID(generation["dataset_id"]))
            datasets = [
                {"id": str(first.id), "immutable_hash": first.immutable_hash},
                other_dataset,
            ]
        bodies = [
            {
                "request_id": identity,
                "dataset_id": row["id"],
                "specification": plan_for(row).model_dump(mode="json"),
            }
            for row in datasets
        ]

    def create(item):
        index, prefix = item
        return api[0].post(prefix + route, headers=api[1], json=bodies[index])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, enumerate([base, other_base])))
    assert sorted(result.status_code for result in results) == [201, 409]
    winner = next(result.json() for result in results if result.status_code == 201)
    assert winner["id"] == identity
    with api[2].state.sessions() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(model).where(model.id == UUID(identity))
            )
            == 1
        )


def test_client_owned_draft_identity_reconciles_once_and_refuses_changed_intent(
    api, tenant, tmp_path, monkeypatch
):
    base, _, body = prepared(api, tenant, tmp_path, monkeypatch)
    request = {**body, "request_id": str(uuid4())}

    def create(_):
        return api[0].post(base + "/criteria/drafts", headers=api[1], json=request)

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(create, range(3)))
    assert all(result.status_code == 201 for result in results)
    assert {result.json()["id"] for result in results} == {request["request_id"]}
    assert len({result.json()["plan_hash"] for result in results}) == 1
    changed = api[0].post(
        base + "/criteria/drafts",
        headers=api[1],
        json={**request, "requirements": {**request["requirements"], "minimum": 0.1}},
    )
    assert changed.status_code == 409
    with api[2].state.sessions() as session:
        versions = session.scalars(
            select(CriteriaVersionRecord).where(
                CriteriaVersionRecord.id == UUID(request["request_id"])
            )
        ).all()
        assert len(versions) == 1 and versions[0].generation_run_id is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(PayloadSample)
                .where(
                    PayloadSample.metadata_safe["criteria_id"].as_string() == request["request_id"]
                )
            )
            == 1
        )
    forbidden = api[0].post(base + "/criteria/drafts", headers=tenant[2], json=request)
    assert forbidden.status_code == 403


def test_client_owned_plan_identity_preserves_exact_budget_and_one_encrypted_plan(
    api, tenant, tmp_path, monkeypatch
):
    from support_evaluation import plan_for

    from cutback.server.db import DatasetRecord, ExperimentPlanRecord

    base, _, generation = prepared(api, tenant, tmp_path, monkeypatch)
    with api[2].state.sessions() as session:
        dataset = session.get(DatasetRecord, UUID(generation["dataset_id"]))
        specification = plan_for({"immutable_hash": dataset.immutable_hash}).model_dump(mode="json")
    body = {
        "request_id": str(uuid4()),
        "dataset_id": generation["dataset_id"],
        "specification": specification,
    }

    def create(_):
        return api[0].post(base + "/experiments/plans", headers=api[1], json=body)

    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(pool.map(create, range(3)))
    assert all(response.status_code == 201 for response in responses)
    assert {response.json()["id"] for response in responses} == {body["request_id"]}
    assert len({response.json()["plan_hash"] for response in responses}) == 1
    changed = {
        **body,
        "specification": {**specification, "budget": {**specification["budget"], "max_usd": "2"}},
    }
    assert api[0].post(base + "/experiments/plans", headers=api[1], json=changed).status_code == 409
    with api[2].state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ExperimentPlanRecord)
                .where(ExperimentPlanRecord.id == UUID(body["request_id"]))
            )
            == 1
        )
