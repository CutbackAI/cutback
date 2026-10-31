"""Durable client identities cover nonbillable preparation, never inference retries."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from support_agent import fresh_application
from support_evaluation import authorized_dataset, dataset_body

from cutback.evals.datasets import CapturePolicy
from cutback.scanner import scan
from cutback.server.db import CapturePolicyRecord, DatasetRecord, PayloadSample, ScanRun

pytestmark = pytest.mark.postgres


def preparation_request(api, tenant, tmp_path, monkeypatch, kind):
    base, _, dataset, _ = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    if kind == "datasets":
        body = (
            dataset_body(40)
            .model_copy(update={"policy_id": dataset["provenance"]["policy_id"]})
            .model_dump(mode="json")
        )
        return base, body, DatasetRecord, "id"
    if kind == "capture-policies":
        body = CapturePolicy(
            enabled=True,
            approval_ref="owned-retry-test",
            rights_basis="synthetic",
            environments=["staging"],
            workflows=["classify"],
            allowed_providers=["local"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            sample_rate=1,
        ).model_dump(mode="json")
        return base, body, CapturePolicyRecord, "id"
    root = tmp_path / "application"
    root.mkdir()
    fresh_application(root)
    return base, scan(root), ScanRun, "scan_id"


@pytest.mark.parametrize("kind", ["scans", "capture-policies", "datasets"])
def test_same_preparation_identity_is_exact_once_and_changed_body_is_rejected(
    api, tenant, tmp_path, monkeypatch, kind
):
    base, body, table, key = preparation_request(api, tenant, tmp_path, monkeypatch, kind)
    identifier = uuid4()
    path = base + "/" + kind + "?request_id=" + str(identifier)
    first = api[0].post(path, headers=api[1], json=body)
    assert first.status_code == 201
    assert first.json()[key] == str(identifier)
    with api[2].state.sessions() as session:
        before_payloads = session.scalar(select(func.count()).select_from(PayloadSample))
    repeated = api[0].post(path, headers=api[1], json=body)
    assert repeated.status_code == 201 and repeated.json() == first.json()
    with api[2].state.sessions() as session:
        assert (
            session.scalar(select(func.count()).select_from(table).where(table.id == identifier))
            == 1
        )
        assert session.scalar(select(func.count()).select_from(PayloadSample)) == before_payloads
    if kind == "scans":
        changed = {**body, "limitations": [*body["limitations"], "synthetic-changed-input"]}
    elif kind == "datasets":
        changed = {**body, "provenance_ref": "changed-owner-reference"}
    else:
        changed = {**body, "approval_ref": "changed-owner-reference"}
    assert api[0].post(path, headers=api[1], json=changed).status_code == 409


@pytest.mark.parametrize("kind", ["scans", "capture-policies", "datasets"])
def test_concurrent_preparation_reuses_one_scoped_identity(
    api, tenant, tmp_path, monkeypatch, kind
):
    base, body, table, key = preparation_request(api, tenant, tmp_path, monkeypatch, kind)
    identifier = uuid4()
    path = base + "/" + kind + "?request_id=" + str(identifier)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: api[0].post(path, headers=api[1], json=body), range(2)))
    assert all(response.status_code == 201 for response in responses)
    assert responses[0].json() == responses[1].json()
    assert responses[0].json()[key] == str(identifier)
    other = (
        api[0]
        .post("/v1/projects", headers=api[1], json={"slug": "other-" + uuid4().hex})
        .json()["id"]
    )
    conflict = api[0].post(
        f"/v1/projects/{other}/{kind}?request_id={identifier}", headers=api[1], json=body
    )
    assert conflict.status_code == 409
    viewer = api[0].post(path, headers=tenant[2], json=body)
    assert viewer.status_code == 403
    with api[2].state.sessions() as session:
        row = session.get(table, identifier)
        assert row.project_id == UUID(tenant[0])


def test_idempotent_policy_does_not_renew_revoked_rights(api, tenant, tmp_path, monkeypatch):
    base, body, _, _ = preparation_request(api, tenant, tmp_path, monkeypatch, "capture-policies")
    identifier = uuid4()
    path = base + f"/capture-policies?request_id={identifier}"
    assert api[0].post(path, headers=api[1], json=body).status_code == 201
    assert (
        api[0].delete(base + f"/capture-policies/{identifier}", headers=api[1]).status_code == 200
    )
    assert api[0].post(path, headers=api[1], json=body).status_code == 409
    with api[2].state.sessions() as session:
        assert not session.get(CapturePolicyRecord, identifier).enabled


@pytest.mark.parametrize("damage", ["revoked", "expired"])
def test_idempotent_dataset_cannot_rehydrate_expired_or_revoked_content(
    api, tenant, tmp_path, monkeypatch, damage
):
    base, body, _, _ = preparation_request(api, tenant, tmp_path, monkeypatch, "datasets")
    identifier = uuid4()
    path = base + f"/datasets?request_id={identifier}"
    assert api[0].post(path, headers=api[1], json=body).status_code == 201
    if damage == "revoked":
        response = api[0].delete(base + "/capture-policies/" + body["policy_id"], headers=api[1])
        assert response.status_code == 200
    else:
        from cutback.server.db import DatasetItem

        with api[2].state.sessions.begin() as session:
            payload_ids = select(DatasetItem.payload_id).where(DatasetItem.dataset_id == identifier)
            for row in session.scalars(
                select(PayloadSample).where(PayloadSample.id.in_(payload_ids))
            ):
                row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    response = api[0].post(path, headers=api[1], json=body)
    assert response.status_code == (403 if damage == "revoked" else 409)
