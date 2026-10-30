"""Actual encrypted chunk transport, bounded finalization and shared PostgreSQL quotas."""

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from integration.test_semantic_cache_workflow import prepare_namespace
from sqlalchemy import func, select

from cutback.evals.datasets import Case, digest
from cutback.server.db import CapturePolicyRecord, DatasetRecord, PayloadSample
from cutback.server.evaluation_api import policy_object_count
from cutback.server.semantic_upload_db import SemanticDatasetUpload
from cutback.server.semantic_upload_worker import run_semantic_upload_one

pytestmark = pytest.mark.postgres


def setup_upload(api, tenant, tmp_path, monkeypatch, *, count=16, dimensions=2, attempts=1):
    path, body, ns, _, _ = prepare_namespace(
        api, tenant, tmp_path, monkeypatch, dimensions=dimensions
    )
    cases = [
        Case(
            id="upload-" + str(index),
            group="upload-family-" + str(index),
            input=json.dumps(
                {
                    "query": "Owned upload control " + chr(0x400 + index),
                    "vector": [0.03125] * dimensions,
                    "embedding_contract_sha256": ns["embedding_contract_sha256"],
                    "baseline_status": "completed",
                    "baseline_output": "billing",
                }
            ),
            gold="billing",
        ).model_dump(mode="json")
        for index in range(count)
    ]
    metadata = {
        "workflow": "classify",
        "environment": "staging",
        "policy_id": body["policy_id"],
        "source_type": "synthetic_fixture",
        "independent_labels": True,
        "representative": False,
        "provenance_ref": "owned-chunked-controls",
        "split_policy": "task-aware-v2",
        "split_seed": 17,
    }
    prepared = {
        "metadata": metadata,
        "expected_cases": count,
        "expected_cases_sha256": digest(cases),
        "max_bytes": 33554432,
        "max_finalize_seconds": 120,
        "max_finalize_attempts": attempts,
        "content_upload_approved": True,
    }
    endpoint = path + "/semantic-cache/namespaces/" + ns["id"] + "/dataset-uploads"
    response = api[0].post(endpoint, headers=api[1], json=prepared)
    assert response.status_code == 201, response.text
    receipt = response.json()
    route = path + "/semantic-cache/dataset-uploads/" + receipt["id"]
    return route, receipt, cases, metadata, endpoint, prepared


def append_all(api, route, receipt, cases):
    for offset in range(0, len(cases), 8):
        response = api[0].post(
            route + "/append",
            headers=api[1],
            json={
                "approval_hash": receipt["approval_hash"],
                "offset": offset,
                "cases": cases[offset : offset + 8],
            },
        )
        assert response.status_code == 200, response.text
    return response.json()


def seal(api, route, receipt):
    response = api[0].post(
        route + "/finalize", headers=api[1], json={"approval_hash": receipt["approval_hash"]}
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_real_dense_vector_corpus_exceeds_request_limit_but_chunks_reuse_exact_registered_objects(
    api, tenant, tmp_path, monkeypatch
):
    route, receipt, cases, metadata, _, _ = setup_upload(
        api, tenant, tmp_path, monkeypatch, count=160, dimensions=1536
    )
    assert len(json.dumps(cases).encode()) > 1048576
    store = api[2].state.artifact_store
    writes = []
    original = store.put

    def recording(*args):
        result = original(*args)
        writes.append(result)
        return result

    monkeypatch.setattr(store, "put", recording)
    # Existing direct import limit stays in force. No blanket request-cap increase.
    rejected = api[0].post(
        "/v1/projects/" + tenant[0] + "/datasets", headers=api[1], json={**metadata, "cases": cases}
    )
    assert rejected.status_code == 413
    assert not writes
    appended = append_all(api, route, receipt, cases)
    assert appended["received_cases"] == len(cases) and len(writes) == len(cases)
    seal(api, route, receipt)
    outcome = run_semantic_upload_one(
        api[2].state.sessions, store, execution_enabled=True, only_upload_id=receipt["id"]
    )
    assert outcome["status"] == "FINALIZED", outcome
    assert outcome["billable_inference_calls"] == 0 and outcome["data_plane_cost_usd"] is None
    assert len(writes) == len(cases)  # Finalization must not copy/re-encrypt the corpus.
    again = seal(api, route, receipt)
    assert again["dataset_id"] == outcome["dataset_id"]
    assert (
        run_semantic_upload_one(
            api[2].state.sessions, store, execution_enabled=True, only_upload_id=receipt["id"]
        )["status"]
        == "NO_ELIGIBLE_SEMANTIC_UPLOAD"
    )
    with api[2].state.sessions() as session:
        assert session.scalar(
            select(func.count())
            .select_from(PayloadSample)
            .where(PayloadSample.project_id == UUID(tenant[0]))
        ) == len(cases)
        assert policy_object_count(session, UUID(metadata["policy_id"])) == len(cases)
        dataset = session.get(DatasetRecord, UUID(outcome["dataset_id"]))
        assert dataset.provenance["case_count"] == len(cases)
        assert dataset.provenance["semantic_upload_hash"] == receipt["approval_hash"]


def test_offsets_replays_quota_reservations_and_cancellation_are_scoped(
    api, tenant, tmp_path, monkeypatch
):
    route, receipt, cases, metadata, endpoint, prepared = setup_upload(
        api, tenant, tmp_path, monkeypatch
    )
    with api[2].state.sessions() as session:
        assert policy_object_count(session, UUID(metadata["policy_id"])) == 16
    chunk = {"approval_hash": receipt["approval_hash"], "offset": 0, "cases": cases[:8]}
    assert api[0].post(route + "/append", headers=tenant[2], json=chunk).status_code == 403
    assert api[0].get(route.replace(tenant[0], str(uuid4())), headers=tenant[2]).status_code == 403
    assert (
        api[0].post(route + "/append", headers=api[1], json={**chunk, "offset": 1}).status_code
        == 409
    )
    assert api[0].post(route + "/append", headers=api[1], json=chunk).status_code == 200
    with api[2].state.sessions() as session:
        count = session.scalar(
            select(func.count())
            .select_from(PayloadSample)
            .where(PayloadSample.project_id == UUID(tenant[0]))
        )
        assert count == 8 and policy_object_count(session, UUID(metadata["policy_id"])) == 16
    assert api[0].post(route + "/append", headers=api[1], json=chunk).status_code == 200
    assert (
        api[0]
        .post(
            route + "/append",
            headers=api[1],
            json={**chunk, "cases": [{**cases[0], "gold": "other"}]},
        )
        .status_code
        == 409
    )
    assert (
        api[0]
        .post(route + "/finalize", headers=api[1], json={"approval_hash": receipt["approval_hash"]})
        .status_code
        == 409
    )
    # Other writers consume the same reserved object allowance.
    with api[2].state.sessions.begin() as session:
        policy = session.get(CapturePolicyRecord, UUID(metadata["policy_id"]))
        policy.policy = {**policy.policy, "max_samples": 16}
    assert api[0].post(endpoint, headers=api[1], json=prepared).status_code == 429
    cancelled = api[0].post(
        route + "/cancel", headers=api[1], json={"approval_hash": receipt["approval_hash"]}
    )
    assert cancelled.status_code == 200 and cancelled.json()["state"] == "CANCELLED"
    with api[2].state.sessions() as session:
        assert policy_object_count(session, UUID(metadata["policy_id"])) == 8
        assert all(
            payload.expires_at <= datetime.now(UTC)
            for payload in session.scalars(
                select(PayloadSample).where(PayloadSample.project_id == UUID(tenant[0]))
            )
        )
    assert api[0].post(route + "/append", headers=api[1], json=chunk).status_code == 409


def test_sequence_tampering_and_crash_recovery_cannot_create_multiple_datasets(
    api, tenant, tmp_path, monkeypatch
):
    route, receipt, cases, _, _, _ = setup_upload(api, tenant, tmp_path, monkeypatch, attempts=2)
    append_all(api, route, receipt, [{**cases[0], "gold": "tampered"}, *cases[1:]])
    seal(api, route, receipt)
    store = api[2].state.artifact_store
    outcome = run_semantic_upload_one(
        api[2].state.sessions, store, execution_enabled=True, only_upload_id=receipt["id"]
    )
    assert (
        outcome["status"] == "FAILED"
        and outcome["code"] == "SEMANTIC_UPLOAD_COMPLETE_SEQUENCE_HASH_MISMATCH"
    )
    with api[2].state.sessions() as session:
        assert not session.get(SemanticDatasetUpload, UUID(receipt["id"])).dataset_id

    # A crashed deterministic read may resume only within the explicitly frozen attempt bound.
    route2, second, cases2, _, _, _ = setup_upload(
        api, tenant, tmp_path / "second", monkeypatch, attempts=2
    )
    append_all(api, route2, second, cases2)
    seal(api, route2, second)
    with api[2].state.sessions.begin() as session:
        row = session.get(SemanticDatasetUpload, UUID(second["id"]))
        row.lease_token, row.lease_until = uuid4(), datetime.now(UTC) - timedelta(seconds=1)
        row.attempts = 1
    outcome2 = run_semantic_upload_one(
        api[2].state.sessions,
        api[2].state.artifact_store,
        execution_enabled=True,
        only_upload_id=second["id"],
    )
    assert outcome2["status"] == "FINALIZED", outcome2
    status = api[0].get(route2, headers=tenant[2]).json()
    assert status["attempts"] == 2 and status["uncertain_attempts"] == 1


def test_committed_append_and_finalization_survive_lost_acknowledgements(
    api, tenant, tmp_path, monkeypatch
):
    route, receipt, cases, _, _, _ = setup_upload(api, tenant, tmp_path, monkeypatch)
    sessions, store = api[2].state.sessions, api[2].state.artifact_store
    original_begin = sessions.begin
    lose_append = True
    lose_finalize = False

    @contextmanager
    def uncertain_commit():
        nonlocal lose_append, lose_finalize
        with original_begin() as session:
            yield session
            appended = any(isinstance(row, PayloadSample) for row in session.identity_map.values())
            finalized = any(
                isinstance(row, SemanticDatasetUpload) and row.state == "FINALIZED"
                for row in session.identity_map.values()
            )
        if lose_append and appended:
            lose_append = False
            raise OSError("synthetic lost append commit acknowledgement")
        if lose_finalize and finalized:
            lose_finalize = False
            raise OSError("synthetic lost finalization commit acknowledgement")

    monkeypatch.setattr(sessions, "begin", uncertain_commit)
    chunk = {"approval_hash": receipt["approval_hash"], "offset": 0, "cases": cases[:8]}
    assert api[0].post(route + "/append", headers=api[1], json=chunk).status_code == 500
    assert api[0].get(route, headers=api[1]).json()["received_cases"] == 8
    # content_transaction must preserve committed registered objects after an uncertain ACK.
    with sessions() as session:
        rows = session.scalars(
            select(PayloadSample).where(PayloadSample.project_id == UUID(tenant[0]))
        ).all()
        assert len(rows) == 8
        for row in rows:
            assert (
                digest(json.loads(store.get(UUID(tenant[0]), row.object_ref))) == row.content_hash
            )
    assert api[0].post(route + "/append", headers=api[1], json=chunk).status_code == 200
    assert (
        api[0]
        .post(route + "/append", headers=api[1], json={**chunk, "offset": 8, "cases": cases[8:]})
        .status_code
        == 200
    )
    seal(api, route, receipt)
    lose_finalize = True
    completed = run_semantic_upload_one(
        sessions, store, execution_enabled=True, only_upload_id=receipt["id"]
    )
    assert completed["status"] == "FINALIZED", completed
    assert seal(api, route, receipt)["dataset_id"] == completed["dataset_id"]
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(DatasetRecord)
                .where(DatasetRecord.project_id == UUID(tenant[0]))
            )
            == 1
        )


def test_chunk_finalization_matches_direct_import_content_lineage_and_split_assignment(
    api, tenant, tmp_path, monkeypatch
):
    route, receipt, cases, metadata, _, _ = setup_upload(
        api, tenant, tmp_path, monkeypatch, count=32
    )
    append_all(api, route, receipt, cases)
    seal(api, route, receipt)
    completed = run_semantic_upload_one(
        api[2].state.sessions,
        api[2].state.artifact_store,
        execution_enabled=True,
        only_upload_id=receipt["id"],
    )
    assert completed["status"] == "FINALIZED", completed
    direct = api[0].post(
        "/v1/projects/" + tenant[0] + "/datasets", headers=api[1], json={**metadata, "cases": cases}
    )
    assert direct.status_code == 201, direct.text
    with api[2].state.sessions() as session:
        uploaded = session.get(DatasetRecord, UUID(completed["dataset_id"]))
        imported = session.get(DatasetRecord, UUID(direct.json()["id"]))
        for key in (
            "content_hash",
            "assignment_hash",
            "split_counts",
            "group_count",
            "split_strategy",
        ):
            assert uploaded.provenance[key] == imported.provenance[key]
        # The full version also binds the truthful upload provenance extension.
        assert uploaded.immutable_hash != imported.immutable_hash


def test_concurrent_uploads_cannot_overreserve_shared_policy_capacity(
    api, tenant, tmp_path, monkeypatch
):
    _, _, _, metadata, endpoint, prepared = setup_upload(api, tenant, tmp_path, monkeypatch)
    with api[2].state.sessions.begin() as session:
        policy = session.get(CapturePolicyRecord, UUID(metadata["policy_id"]))
        policy.policy = {**policy.policy, "max_samples": 32}

    def prepare():
        return api[0].post(endpoint, headers=api[1], json=prepared).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: prepare(), range(2)))
    assert sorted(outcomes) == [201, 429]
    with api[2].state.sessions() as session:
        assert policy_object_count(session, UUID(metadata["policy_id"])) == 32
