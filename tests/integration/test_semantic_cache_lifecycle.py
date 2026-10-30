"""Current authority, explicit content review, deletion and conservative coverage."""

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from integration.test_semantic_cache_workflow import prepare_namespace
from integration.test_semantic_dataset_upload import append_all, setup_upload
from sqlalchemy import func, select

from cutback.server.db import AccessKey, AuditEvent, Project
from cutback.server.enterprise_db import CacheEntry
from cutback.server.semantic_cache_db import SemanticCacheMember, SemanticCacheNamespace
from cutback.server.semantic_upload_db import SemanticDatasetUpload
from cutback.server.semantic_upload_worker import run_semantic_upload_one

pytestmark = pytest.mark.postgres


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


def test_catalog_purpose_review_uncertain_commit_and_namespace_delete(
    api, tenant, tmp_path, monkeypatch
):
    path, _, ns, _, entry = prepare_namespace(api, tenant, tmp_path, monkeypatch)
    sessions, store = api[2].state.sessions, api[2].state.artifact_store
    original = sessions.begin
    lose_ack = True

    @contextmanager
    def uncertain_commit():
        nonlocal lose_ack
        with original() as session:
            yield session
            catalog_written = any(
                isinstance(row, CacheEntry) for row in session.identity_map.values()
            )
        if lose_ack and catalog_written:
            lose_ack = False
            raise OSError("synthetic committed catalog lost acknowledgement")

    monkeypatch.setattr(sessions, "begin", uncertain_commit)
    assert (
        api[0].post("/v1/ingest/semantic-cache/put", headers=tenant[1], json=entry).status_code
        == 500
    )
    route = path + "/semantic-cache/namespaces/" + ns["id"]
    listed = api[0].get(route + "/entries", headers=tenant[2])
    assert listed.status_code == 200 and len(listed.json()) == 1
    assert entry["query"] not in listed.text and entry["response"] not in listed.text
    content = route + "/entries/" + listed.json()[0]["id"] + "/content"
    assert (
        api[0]
        .post(content, headers=tenant[2], json={"purpose": "owned-curation-audit"})
        .status_code
        == 403
    )
    assert api[0].post(content, headers=api[1], json={}).status_code == 422
    reviewed = api[0].post(content, headers=api[1], json={"purpose": "owned-curation-audit"})
    assert reviewed.status_code == 200 and reviewed.json()["query"] == entry["query"]
    assert reviewed.json()["response"] == entry["response"]
    with sessions() as session:
        cached = session.get(CacheEntry, UUID(listed.json()[0]["id"]))
        ref = cached.object_ref
        assert store.get(UUID(tenant[0]), ref)
        assert session.scalar(
            select(AuditEvent.id).where(
                AuditEvent.project_id == UUID(tenant[0]),
                AuditEvent.action == "semantic_cache_content_reviewed",
            )
        )
    exported = api[0].get(path + "/export", headers=api[1])
    assert (
        "semantic_cache_namespaces" in exported.text and "semantic_cache_members" in exported.text
    )
    assert entry["query"] not in exported.text
    denied = api[0].request(
        "DELETE",
        route,
        headers=api[1],
        json={"approval_hash": "0" * 64, "approval_ref": "owned-delete"},
    )
    assert denied.status_code == 409
    deleted = api[0].request(
        "DELETE",
        route,
        headers=api[1],
        json={"approval_hash": ns["specification_hash"], "approval_ref": "owned-delete"},
    )
    assert deleted.status_code == 200, deleted.text
    with sessions() as session:
        assert not session.get(CacheEntry, UUID(listed.json()[0]["id"]))
        assert not session.scalar(
            select(func.count())
            .select_from(SemanticCacheMember)
            .where(SemanticCacheMember.project_id == UUID(tenant[0]))
        )
    with pytest.raises((OSError, ValueError)):
        store.get(UUID(tenant[0]), ref)


def test_upload_revoked_serving_key_and_attempt_bound_never_finalize(
    api, tenant, tmp_path, monkeypatch
):
    route, receipt, cases, _, _, _ = setup_upload(api, tenant, tmp_path, monkeypatch)
    append_all(api, route, receipt, cases)
    assert (
        api[0]
        .post(route + "/finalize", headers=api[1], json={"approval_hash": receipt["approval_hash"]})
        .status_code
        == 202
    )
    with api[2].state.sessions.begin() as session:
        row = session.get(SemanticDatasetUpload, UUID(receipt["id"]))
        row.lease_token, row.lease_until, row.attempts = (
            uuid4(),
            datetime.now(UTC) - timedelta(seconds=1),
            1,
        )
    outcome = run_semantic_upload_one(
        api[2].state.sessions,
        api[2].state.artifact_store,
        execution_enabled=True,
        only_upload_id=receipt["id"],
    )
    assert outcome["status"] == "NO_ELIGIBLE_SEMANTIC_UPLOAD"
    status = api[0].get(route, headers=api[1]).json()
    assert status["state"] == "FAILED" and status["uncertain_attempts"] == status["attempts"] == 1
    assert status["dataset_id"] is None

    second_route, second, second_cases, _, _, _ = setup_upload(
        api, tenant, tmp_path / "revoked", monkeypatch
    )
    with api[2].state.sessions.begin() as session:
        row = session.get(SemanticDatasetUpload, UUID(second["id"]))
        key_id = UUID(
            session.get(SemanticCacheNamespace, row.namespace_id).specification["serving_key_id"]
        )
        session.get(AccessKey, key_id).revoked_at = datetime.now(UTC)
    response = api[0].post(
        second_route + "/append",
        headers=api[1],
        json={"approval_hash": second["approval_hash"], "offset": 0, "cases": second_cases[:8]},
    )
    assert response.status_code == 409
    assert (
        api[0]
        .post(
            second_route + "/cancel",
            headers=api[1],
            json={"approval_hash": second["approval_hash"]},
        )
        .status_code
        == 200
    )


def test_populated_upload_blocks_downgrade_then_project_delete_clears_every_scoped_reference(
    api, tenant, tmp_path, monkeypatch
):
    route, receipt, cases, _, _, _ = setup_upload(api, tenant, tmp_path, monkeypatch)
    append_all(api, route, receipt, cases)
    cfg = Config()
    cfg.set_main_option("script_location", str(files("cutback.server").joinpath("migrations")))
    with pytest.raises(RuntimeError, match="EXPLICITLY_CANCEL_OR_REMOVE_DATASET_UPLOADS"):
        command.downgrade(cfg, "0022")
    with api[2].state.sessions() as session:
        slug = session.get(Project, UUID(tenant[0])).slug
    deleted = api[0].delete(
        "/v1/projects/" + tenant[0], headers=api[1], params={"confirm_slug": slug}
    )
    assert deleted.status_code == 200, deleted.text
    command.downgrade(cfg, "0021")
    command.upgrade(cfg, "head")
    command.check(cfg)


def test_numeric_query_ids_and_vector_baseline_noise_cannot_manufacture_heldout_coverage(
    api, tenant, tmp_path, monkeypatch
):
    path, body, ns, _, entry = prepare_namespace(api, tenant, tmp_path, monkeypatch)
    assert (
        api[0].post("/v1/ingest/semantic-cache/put", headers=tenant[1], json=entry).status_code
        == 201
    )
    cases = []
    for index in range(300):
        symbol = "".join(chr(ord("a") + index // (26**power) % 26) for power in range(3))
        cases.append(
            {
                "id": "noise-" + symbol,
                "group": "declared-" + symbol,
                "gold": "billing" if index % 2 else "access",
                "input": json.dumps(
                    {
                        "query": "Owned repeated question " + str(index),
                        "vector": [1.0, index / 300],
                        "embedding_contract_sha256": ns["embedding_contract_sha256"],
                        "baseline_status": "completed",
                        "baseline_output": "varied fallback " + symbol,
                    }
                ),
            }
        )
    imported = api[0].post(
        path + "/datasets",
        headers=api[1],
        json={
            "workflow": "classify",
            "environment": "staging",
            "policy_id": body["policy_id"],
            "source_type": "synthetic_fixture",
            "independent_labels": True,
            "provenance_ref": "owned-numeric-coverage-regression",
            "split_policy": "task-aware-v2",
            "cases": cases,
        },
    )
    assert imported.status_code == 201, imported.text
    assert imported.json()["provenance"]["group_count"] == 300  # Raw JSON alone looks distinct.
    response = api[0].post(
        path + "/semantic-cache/namespaces/" + ns["id"] + "/qualifications",
        headers=api[1],
        json={
            "dataset_id": imported.json()["id"],
            "specification": {
                "rule": body["rule"],
                "thresholds": [0.5, 0.99],
                "maximum_false_positive_group_upper95": 0.1,
                "minimum_useful_hit_group_ratio": 0.1,
            },
        },
    )
    assert response.status_code == 409, response.text
    assert "DISJOINT_CALIBRATION_AND_CONFIRMATION_GROUPS_REQUIRED" in response.json()["code"]
