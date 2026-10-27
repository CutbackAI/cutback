from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from support_evaluation import authorized_dataset

from cutback.server.db import CapturePolicyRecord, DatasetItem, DatasetRecord, PayloadSample
from cutback.server.review_db import (
    ReviewItem,
    ReviewLease,
    ReviewQueue,
    ReviewResolution,
    ReviewVote,
)
from cutback.server.worker import load_dataset

pytestmark = pytest.mark.postgres


@pytest.fixture
def review_setup(api, tenant, tmp_path, monkeypatch):
    client, admin, app = api
    path, _, dataset, _ = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    policy = dataset["provenance"]["policy_id"]
    credentials = []
    for _ in range(4):
        response = client.post(
            path + "/keys", headers=admin, json={"role": "analyst", "environment": "staging"}
        )
        assert response.status_code == 201
        key = response.json()
        credentials.append((key["key_id"], {"Authorization": "Bearer " + key["token"]}))
    with app.state.sessions() as session:
        source = session.scalar(
            select(DatasetItem)
            .where(DatasetItem.dataset_id == UUID(dataset["id"]))
            .order_by(DatasetItem.id)
        )
        source_id = str(source.id)
    body = {
        "policy_id": policy,
        "workflow": "classify",
        "environment": "staging",
        "sources": [{"kind": "dataset_item", "id": source_id}],
        "rubric": "Independently classify this synthetic fruit. Return A or B.",
        "reviewers": [actor for actor, _ in credentials[:3]],
        "adjudicator": credentials[3][0],
        "required_reviewers": 2,
        "mode": "labeling",
        "provenance_ref": "synthetic-review-test",
    }
    return path, policy, dataset, credentials, body


def create_queue(client, admin, path, body):
    response = client.post(path + "/reviews", headers=admin, json=body)
    assert response.status_code == 201, response.text
    q = response.json()
    return path + "/reviews/" + q["id"], q


def claim(client, base, headers):
    response = client.post(base + "/claim", headers=headers, json={"lease_seconds": 30})
    assert response.status_code == 200, response.text
    lease = response.json()
    return base + "/items/" + lease["item_id"], lease


def submit(client, item, headers, lease, gold="A"):
    return client.post(
        item + "/votes",
        headers=headers,
        json={
            "lease_id": lease["lease_id"],
            "queue_hash": lease["queue_hash"],
            "decision": "label",
            "gold": gold,
            "rationale": "Independent synthetic judgment for this fixture.",
        },
    )


def test_blind_disagreement_adjudication_and_atomic_new_dataset(api, tenant, review_setup):
    client, admin, app = api
    path, _, original, creds, body = review_setup
    assert client.post(path + "/reviews", headers=creds[0][1], json=body).status_code == 403
    assert client.get(path + "/reviews/principals", headers=creds[0][1]).status_code == 403
    directory = client.get(path + "/reviews/principals", headers=admin).json()
    assert {c[0] for c in creds}.issubset({p["actor"] for p in directory["items"]})
    assert all("token" not in p and "digest" not in p for p in directory["items"])
    base, q = create_queue(client, admin, path, body)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(
            pool.map(
                lambda h: client.post(base + "/claim", headers=h, json={}),
                [c[1] for c in creds[:3]],
            )
        )
    assert sorted(r.status_code for r in results) == [200, 200, 409]
    assigned = [(creds[i][1], r.json()) for i, r in enumerate(results) if r.status_code == 200]
    h1, lease1 = assigned[0]
    h2, lease2 = assigned[1]
    item = base + "/items/" + lease1["item_id"]
    assert lease1["item_id"] == lease2["item_id"]
    # Even a bootstrap administrator cannot read peer answers through reviewer endpoints.
    for headers in (admin, tenant[2], creds[3][1]):
        assert (
            client.post(
                item + "/content", headers=headers, json={"purpose": "blind-review"}
            ).status_code
            == 403
        )
    content = client.post(item + "/content", headers=h1, json={"purpose": "blind-review"}).json()
    assert (
        content["source_gold"] == content["peer_answers"] == "withheld"
        and content["own_vote"] is None
    )
    assert (
        client.post(
            item + "/adjudication-content", headers=creds[3][1], json={"purpose": "adjudication"}
        ).status_code
        == 409
    )
    first = submit(client, item, h1, lease1, "A")
    assert first.status_code == 201 and first.json()["state"] == "reviewed"
    own = client.post(item + "/content", headers=h2, json={"purpose": "blind-review"}).json()
    assert own["own_vote"] is None and "votes" not in own
    assert submit(client, item, h1, lease1, "A").json()["status"] == "duplicate"
    assert submit(client, item, h1, lease1, "B").status_code == 409
    second = submit(client, item, h2, lease2, "B")
    assert second.status_code == 201 and second.json()["state"] == "adjudication"
    assert (
        client.post(
            item + "/adjudication-content", headers=h1, json={"purpose": "peek"}
        ).status_code
        == 403
    )
    decision = client.post(
        item + "/adjudication-content",
        headers=creds[3][1],
        json={"purpose": "resolve-disagreement"},
    )
    assert decision.status_code == 200 and len(decision.json()["votes"]) == 2
    adjudication = {
        "queue_hash": q["version_hash"],
        "decision": "label",
        "gold": "A",
        "rationale": "Resolved using the frozen synthetic rubric.",
    }
    assert (
        client.post(item + "/adjudicate", headers=creds[3][1], json=adjudication).status_code == 201
    )
    assert (
        client.post(item + "/adjudicate", headers=creds[3][1], json=adjudication).status_code == 409
    )
    promotion = {
        "queue_hash": q["version_hash"],
        "provenance_ref": "synthetic-reviewed-v1",
        "rights_confirmed": True,
        "independent_reviewers_attested": True,
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        promoted = list(
            pool.map(
                lambda _: client.post(base + "/promote", headers=admin, json=promotion), range(2)
            )
        )
    assert all(r.status_code == 201 for r in promoted), [r.text for r in promoted]
    assert promoted[0].json()["id"] == promoted[1].json()["id"] != original["id"]
    assert (
        client.post(
            base + "/promote",
            headers=admin,
            json={**promotion, "independent_reviewers_attested": False},
        ).status_code
        == 409
    )
    with app.state.sessions() as session:
        dataset = session.get(DatasetRecord, UUID(promoted[0].json()["id"]))
        assert dataset.provenance["review_receipts"][0]["method"] == "adjudication"
        assert dataset.provenance["synthetic"] is True and dataset.provenance["independent_labels"]
        loaded, _ = load_dataset(session, dataset, app.state.artifact_store)
        assert loaded["items"][0]["case"]["gold"] == "A"
        assert (
            session.get(DatasetRecord, UUID(original["id"])).immutable_hash
            == original["immutable_hash"]
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(ReviewVote)
                .where(ReviewVote.project_id == UUID(tenant[0]))
            )
            == 2
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(ReviewResolution)
                .where(ReviewResolution.project_id == UUID(tenant[0]))
            )
            == 1
        )
    export = client.get(path + "/export", headers=admin)
    assert "Independent synthetic judgment" not in export.text and body["rubric"] not in export.text
    assert not any(
        b"Independent synthetic judgment" in p.read_bytes()
        for p in app.state.artifact_store.root.rglob("*")
        if p.is_file()
    )


def test_expiry_release_revocation_immutable_config_and_cross_tenant(api, tenant, review_setup):
    client, admin, app = api
    path, policy, _, creds, body = review_setup
    assert (
        client.post(
            path + "/reviews", headers=admin, json={**body, "adjudicator": creds[0][0]}
        ).status_code
        == 422
    )
    base, q = create_queue(client, admin, path, body)
    item, lease = claim(client, base, creds[0][1])
    with app.state.sessions.begin() as session:
        session.get(ReviewLease, UUID(lease["lease_id"])).expires_at = datetime.now(
            UTC
        ) - timedelta(seconds=1)
    assert submit(client, item, creds[0][1], lease).status_code == 409
    assert (
        client.post(item + "/content", headers=creds[0][1], json={"purpose": "expired"}).status_code
        == 403
    )
    item, replacement = claim(client, base, creds[0][1])
    assert replacement["lease_id"] != lease["lease_id"]
    assert (
        client.post(
            item + "/release",
            headers=creds[0][1],
            json={"lease_id": replacement["lease_id"], "purpose": "give-back"},
        ).status_code
        == 200
    )
    assert submit(client, item, creds[0][1], replacement).status_code == 409
    item, active = claim(client, base, creds[0][1])
    assert client.delete(path + "/keys/" + creds[0][0], headers=admin).status_code == 200
    assert submit(client, item, creds[0][1], active).status_code == 401
    item2, _ = claim(client, base, creds[2][1])
    assert item2 == item
    other = client.post(
        "/v1/projects", headers=admin, json={"slug": "review-other-" + uuid4().hex}
    ).json()["id"]
    assert (
        client.post(base.replace(tenant[0], other) + "/claim", headers=admin, json={}).status_code
        == 404
    )
    with app.state.sessions.begin() as session:
        row = session.get(CapturePolicyRecord, UUID(policy))
        row.enabled = False
    assert (
        client.post(
            item + "/content", headers=creds[2][1], json={"purpose": "revoked-policy"}
        ).status_code
        == 403
    )


def test_consensus_calibration_is_hidden_and_cannot_become_confirmation_data(api, review_setup):
    client, admin, _ = api
    path, _, _, creds, body = review_setup
    base, q = create_queue(client, admin, path, {**body, "mode": "calibration"})
    for _, headers in creds[:2]:
        item, lease = claim(client, base, headers)
        source = client.post(
            item + "/content", headers=headers, json={"purpose": "calibrate"}
        ).json()
        assert source["source_gold"] == "withheld"
        response = submit(client, item, headers, lease)
        assert response.status_code == 201
    assert response.json()["state"] == "resolved"
    result = client.post(
        base + "/calibration", headers=admin, json={"purpose": "calibration-results"}
    )
    assert result.status_code == 200 and result.json()["synthetic"] is True
    assert all(
        score == {"correct": 1, "total": 1}
        for actor, score in result.json()["scores"].items()
        if actor in body["reviewers"][:2]
    )
    assert (
        client.post(
            base + "/calibration", headers=creds[0][1], json={"purpose": "peek"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            base + "/promote",
            headers=admin,
            json={
                "queue_hash": q["version_hash"],
                "provenance_ref": "invalid-calibration-promotion",
                "rights_confirmed": True,
                "independent_reviewers_attested": True,
            },
        ).status_code
        == 409
    )


def test_review_failed_storage_and_quota_roll_back_objects_and_rows(api, review_setup, monkeypatch):
    client, admin, app = api
    path, policy, _, _, body = review_setup
    original_put = app.state.artifact_store.put
    count = 0

    def fail_second(*args):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("synthetic-private-storage-message")
        return original_put(*args)

    files = {p for p in app.state.artifact_store.root.rglob("*") if p.is_file()}
    monkeypatch.setattr(app.state.artifact_store, "put", fail_second)
    result = client.post(path + "/reviews", headers=admin, json=body)
    assert result.status_code == 500 and "synthetic-private-storage-message" not in result.text
    assert {p for p in app.state.artifact_store.root.rglob("*") if p.is_file()} == files
    with app.state.sessions.begin() as session:
        project_id = UUID(path.rsplit("/", 1)[-1])
        assert (
            session.scalar(
                select(func.count())
                .select_from(ReviewQueue)
                .where(ReviewQueue.project_id == project_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(ReviewItem)
                .where(ReviewItem.project_id == project_id)
            )
            == 0
        )
        row = session.get(CapturePolicyRecord, UUID(policy))
        row.policy = {**row.policy, "max_samples": 41}
    monkeypatch.setattr(app.state.artifact_store, "put", original_put)
    assert client.post(path + "/reviews", headers=admin, json=body).status_code == 429
    assert {p for p in app.state.artifact_store.root.rglob("*") if p.is_file()} == files


def test_trace_failure_review_respects_source_policy_revocation(api, tenant, review_setup):
    from test_workflow_traces import span

    from cutback.server.trace_db import TraceContent

    client, admin, app = api
    path, policy, _, creds, body = review_setup
    long_workflow = "synthetic-" + "w" * 150
    body = {**body, "workflow": long_workflow}
    with app.state.sessions.begin() as session:
        scope_policy = session.get(CapturePolicyRecord, UUID(policy))
        scope_policy.policy = {**scope_policy.policy, "workflows": ["classify", long_workflow]}
    record = span(workflow=long_workflow, status="error")
    assert (
        client.post("/v1/ingest/spans", headers=tenant[1], json={"spans": [record]}).status_code
        == 200
    )
    captured = client.post(
        "/v1/ingest/trace-content",
        headers=tenant[1],
        json={
            "policy_id": policy,
            "sample_id": str(uuid4()),
            "trace_id": record["trace_id"],
            "span_id": record["span_id"],
            "provider": "local",
            "input": "Owned synthetic trace failure",
            "output": "Historical failing output",
            "provenance_ref": "synthetic-trace-review",
        },
    )
    assert captured.status_code == 201, captured.text
    with app.state.sessions() as session:
        link = session.scalar(
            select(TraceContent).where(TraceContent.project_id == UUID(tenant[0]))
        )
        source_id = str(link.id)
    with app.state.sessions.begin() as session:
        source = session.get(CapturePolicyRecord, UUID(policy))
        destination = CapturePolicyRecord(
            project_id=source.project_id,
            enabled=True,
            approved_by="bootstrap_admin",
            policy={**source.policy, "approval_ref": "synthetic-review-copy"},
        )
        session.add(destination)
        session.flush()
        destination_id = str(destination.id)
    base, _ = create_queue(
        client,
        admin,
        path,
        {
            **body,
            "policy_id": destination_id,
            "sources": [{"kind": "trace_content", "id": source_id}],
        },
    )
    item, lease = claim(client, base, creds[0][1])
    response = client.post(
        item + "/content", headers=creds[0][1], json={"purpose": "trace-failure-review"}
    )
    assert (
        response.status_code == 200 and response.json()["input"] == "Owned synthetic trace failure"
    )
    assert response.json()["reference"] == "Historical failing output"
    assert client.delete(path + "/capture-policies/" + policy, headers=admin).status_code == 200
    assert (
        client.post(
            item + "/content", headers=creds[0][1], json={"purpose": "source-revoked"}
        ).status_code
        == 403
    )
    assert submit(client, item, creds[0][1], lease).status_code == 403


def test_oidc_review_assignment_rechecks_live_project_grants(api, tenant, review_setup):
    from test_identity_teams import identity

    from cutback.server.identity import subject_digest

    verifier, token = identity.__wrapped__()
    client, admin, app = api
    app.state.oidc_verifier = verifier
    path, _, _, _, body = review_setup
    organization = client.get(path, headers=admin).json()["organization_id"]
    member = client.post(
        f"/v1/organizations/{organization}/members",
        headers=admin,
        json={"issuer": verifier.issuer, "subject": "synthetic-user"},
    ).json()["id"]
    grant_path = f"/v1/organizations/{organization}/members/{member}/grants"
    assert (
        client.post(
            grant_path, headers=admin, json={"project_id": tenant[0], "role": "analyst"}
        ).status_code
        == 200
    )
    actor = "oidc:" + subject_digest(verifier.issuer, "synthetic-user")[:32]
    body = {**body, "reviewers": [actor, body["reviewers"][0]]}
    base, _ = create_queue(client, admin, path, body)
    oidc = {"Authorization": "Bearer " + token(role="admin")}
    item, lease = claim(client, base, oidc)
    assert client.delete(grant_path + "/" + tenant[0], headers=admin).status_code == 200
    assert submit(client, item, oidc, lease).status_code == 403
    assert (
        client.post(
            item + "/content", headers=oidc, json={"purpose": "revoked-membership"}
        ).status_code
        == 403
    )


def test_project_delete_removes_review_objects_and_receipts(api, tenant, review_setup):
    client, admin, app = api
    path, _, _, creds, body = review_setup
    base, _ = create_queue(client, admin, path, body)
    item, lease = claim(client, base, creds[0][1])
    assert submit(client, item, creds[0][1], lease).status_code == 201
    slug = client.get(path, headers=admin).json()["slug"]
    response = client.delete(path + "?confirm_slug=" + slug, headers=admin)
    assert response.status_code == 200, response.text
    with app.state.sessions() as session:
        for model in (
            ReviewQueue,
            ReviewItem,
            ReviewLease,
            ReviewVote,
            ReviewResolution,
            PayloadSample,
        ):
            assert not session.scalar(
                select(func.count()).select_from(model).where(model.project_id == UUID(tenant[0]))
            )
    assert not any(p.is_file() for p in app.state.artifact_store.root.rglob("*"))


def test_admin_can_close_queue_after_source_expiry_without_reading_content(api, review_setup):
    client, admin, app = api
    path, policy, _, creds, body = review_setup
    base, q = create_queue(client, admin, path, body)
    item, lease = claim(client, base, creds[0][1])
    assert submit(client, item, creds[0][1], lease).status_code == 201
    _, second = claim(client, base, creds[1][1])
    assert (
        client.post(
            base + "/close",
            headers=creds[0][1],
            json={"queue_hash": q["version_hash"], "purpose": "stop"},
        ).status_code
        == 403
    )
    with app.state.sessions.begin() as session:
        session.get(CapturePolicyRecord, UUID(policy)).enabled = False
    metadata = client.get(base + "/items", headers=creds[0][1])
    assert metadata.status_code == 200 and "Independent synthetic judgment" not in metadata.text
    assert (
        client.post(item + "/content", headers=creds[0][1], json={"purpose": "revoked"}).status_code
        == 403
    )
    result = client.post(
        base + "/close",
        headers=admin,
        json={"queue_hash": q["version_hash"], "purpose": "close-revoked-content-queue"},
    )
    assert result.status_code == 200 and result.json()["judgments_deleted"] is False
    with app.state.sessions() as session:
        assert session.get(ReviewLease, UUID(second["lease_id"])).status == "released"
        assert session.get(ReviewQueue, UUID(q["id"])).status == "closed"
        assert (
            session.scalar(
                select(func.count())
                .select_from(ReviewVote)
                .where(ReviewVote.item_id == UUID(lease["item_id"]))
            )
            == 1
        )
