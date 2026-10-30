"""Curated vector cache through actual scoped HTTP, encrypted objects and PostgreSQL."""

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select

from cutback.evals.artifacts import EncryptedFileStore
from cutback.server.db import AccessKey, CapturePolicyRecord, Project, token_hash
from cutback.server.semantic_cache_db import SemanticCacheNamespace
from cutback.server.semantic_cache_worker import run_semantic_cache_one

pytestmark = pytest.mark.postgres


def prepare_namespace(api, tenant, tmp_path, monkeypatch, *, dimensions=2):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    monkeypatch.setenv("CUTBACK_SEMANTIC_CACHE_ENABLED", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    path = "/v1/projects/" + tenant[0]
    policy = api[0].post(
        path + "/capture-policies",
        headers=api[1],
        json={
            "enabled": True,
            "approval_ref": "owned-semantic-cache",
            "rights_basis": "synthetic",
            "environments": ["staging"],
            "workflows": ["classify"],
            "allowed_providers": ["openai"],
            "sample_rate": 1,
            "max_samples": 1000,
            "max_payload_bytes": 65536,
            "expires_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
        },
    )
    assert policy.status_code == 201, policy.text
    with api[2].state.sessions() as session:
        key = session.scalar(
            select(AccessKey).where(AccessKey.digest == token_hash(tenant[1]["Authorization"][7:]))
        )
        key_id = str(key.id)
    rule = {
        "embedding": {
            "adapter": "owned-synthetic-vectors",
            "revision_sha256": "a" * 64,
            "dimensions": dimensions,
        },
        "threshold": 0.5,
    }
    body = {
        "policy_id": policy.json()["id"],
        "serving_key_id": key_id,
        "workflow": "classify",
        "environment": "staging",
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "prompt_revision": "owned-v1",
        "freshness_ref": "owned-catalog-v1",
        "access_context_sha256": "b" * 64,
        "rule": rule,
        "max_entries": 16,
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "curated_catalog_approved": True,
        "encrypted_storage_approved": True,
    }
    response = api[0].post(path + "/semantic-cache/namespaces", headers=api[1], json=body)
    assert response.status_code == 201, response.text
    ns = response.json()
    lookup = {
        "project_id": tenant[0],
        "namespace_id": ns["id"],
        "access_context_sha256": "b" * 64,
        "embedding_contract_sha256": ns["embedding_contract_sha256"],
        "vector": [1.0, 0.0],
    }
    entry = {
        **lookup,
        "query": "Owned curated billing training question",
        "response": "billing",
        "ttl_seconds": 1800,
    }
    return path, body, ns, lookup, entry


def prepare_qualification(api, tenant, path, body, ns):
    cases = []
    contract = ns["embedding_contract_sha256"]
    for index in range(500):
        # Symbolic vector controls exercise gates, not natural-language quality
        # or population independence. Digit-only variations correctly coalesce.
        symbol = "".join(chr(ord("a") + index // (26**power) % 26) for power in range(3))
        positive = index % 2 == 0
        gold = "billing" if positive else "access"
        cases.append(
            {
                "id": "owned-case-" + str(index),
                "group": "owned-family-" + str(index),
                "gold": gold,
                "input": json.dumps(
                    {
                        "query": "Owned symbolic vector control " + symbol,
                        "vector": [1.0, 0.0] if positive else [0.8, 0.6],
                        "embedding_contract_sha256": contract,
                        "baseline_status": "completed",
                        "baseline_output": gold,
                    }
                ),
            }
        )
    response = api[0].post(
        path + "/datasets",
        headers=api[1],
        json={
            "workflow": "classify",
            "environment": "staging",
            "policy_id": body["policy_id"],
            "source_type": "synthetic_fixture",
            "independent_labels": True,
            "provenance_ref": "owned-cache-controls",
            "split_policy": "task-aware-v2",
            "cases": cases,
        },
    )
    assert response.status_code == 201, response.text
    dataset = response.json()
    spec = {
        "rule": body["rule"],
        "thresholds": [0.5, 0.99],
        "maximum_false_positive_group_upper95": 0.1,
        "minimum_useful_hit_group_ratio": 0.1,
    }
    response = api[0].post(
        path + "/semantic-cache/namespaces/" + ns["id"] + "/qualifications",
        headers=api[1],
        json={"dataset_id": dataset["id"], "specification": spec, "max_wall_seconds": 120},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_namespace_consent_context_key_and_encrypted_catalog_are_strict(
    api, tenant, tmp_path, monkeypatch
):
    path, body, ns, lookup, entry = prepare_namespace(api, tenant, tmp_path, monkeypatch)
    assert (
        api[0].post(path + "/semantic-cache/namespaces", headers=tenant[2], json=body).status_code
        == 403
    )
    assert (
        api[0]
        .post(
            path + "/semantic-cache/namespaces",
            headers=api[1],
            json={**body, "encrypted_storage_approved": 1},
        )
        .status_code
        == 422
    )
    assert (
        api[0].post("/v1/ingest/semantic-cache/put", headers=tenant[2], json=entry).status_code
        == 403
    )
    response = api[0].post(
        "/v1/ingest/semantic-cache/put",
        headers=tenant[1],
        json={**entry, "access_context_sha256": "c" * 64},
    )
    assert response.status_code == 409
    response = api[0].post("/v1/ingest/semantic-cache/put", headers=tenant[1], json=entry)
    assert response.status_code == 201, response.text
    assert (
        api[0].post("/v1/ingest/semantic-cache/put", headers=tenant[1], json=entry).status_code
        == 409
    )
    response = api[0].post("/v1/ingest/semantic-cache/lookup", headers=tenant[1], json=lookup)
    assert (
        response.status_code == 200
        and not response.json()["hit"]
        and response.json()["response"] is None
    )
    assert "QUALIFIED_ACTIVATION" in response.json()["reason"]
    other = api[0].post(path + "/keys", headers=api[1], json={"role": "ingest"}).json()
    denied = api[0].post(
        "/v1/ingest/semantic-cache/lookup",
        headers={"Authorization": "Bearer " + other["token"]},
        json=lookup,
    )
    assert not denied.json()["hit"] and "KEY_OR_SCOPE_DENIED" in denied.json()["reason"]
    exported = api[0].get(path + "/export", headers=api[1]).content
    assert entry["query"].encode() not in exported


def test_actual_calibration_confirmation_and_exact_activation_preserve_utility_and_unknown_costs(
    api, tenant, tmp_path, monkeypatch
):
    path, body, ns, lookup, entry = prepare_namespace(api, tenant, tmp_path, monkeypatch)
    assert (
        api[0].post("/v1/ingest/semantic-cache/put", headers=tenant[1], json=entry).status_code
        == 201
    )
    q = prepare_qualification(api, tenant, path, body, ns)
    route = path + "/semantic-cache/qualifications/" + q["id"]
    approved = api[0].post(
        route + "/approve",
        headers=api[1],
        json={
            "approval_hash": q["approval_hash"],
            "approval_ref": "owned-bounded-local-calibration",
        },
    )
    assert approved.status_code == 202, approved.text
    assert (
        api[0]
        .post(
            route + "/approve",
            headers=api[1],
            json={"approval_hash": q["approval_hash"], "approval_ref": "duplicate"},
        )
        .status_code
        == 409
    )
    first = run_semantic_cache_one(
        api[2].state.sessions,
        api[2].state.artifact_store,
        execution_enabled=True,
        only_qualification_id=q["id"],
    )
    assert first["status"] == "CALIBRATED", first
    q = api[0].get(route, headers=api[1]).json()
    assert q["selected_threshold"] == 0.99
    assert q["report"]["calibration"]["trials"][0]["assessment"]["passed"] is False
    second = run_semantic_cache_one(
        api[2].state.sessions,
        api[2].state.artifact_store,
        execution_enabled=True,
        only_qualification_id=q["id"],
    )
    assert second["status"] == "PASSED", second
    q = api[0].get(route, headers=api[1]).json()
    assert q["report"]["confirmation"]["false_positive"] == 0
    assert q["report"]["confirmation"]["complete_task_cost_usd"] is None
    activation = api[0].get(route + "/activation", headers=api[1]).json()
    wrong = api[0].post(
        route + "/activate",
        headers=api[1],
        json={"approval_hash": "0" * 64, "approval_ref": "wrong"},
    )
    assert wrong.status_code == 409
    response = api[0].post(
        route + "/activate",
        headers=api[1],
        json={
            "approval_hash": activation["activation_hash"],
            "approval_ref": "owned-curated-serving-approval",
        },
    )
    assert response.status_code == 200, response.text
    hit = api[0].post("/v1/ingest/semantic-cache/lookup", headers=tenant[1], json=lookup)
    assert hit.status_code == 200 and hit.json()["hit"] and hit.json()["response"] == "billing", (
        hit.text
    )
    assert hit.json()["realized_savings_usd"] is None
    miss = api[0].post(
        "/v1/ingest/semantic-cache/lookup", headers=tenant[1], json={**lookup, "vector": [0.8, 0.6]}
    )
    assert not miss.json()["hit"] and miss.json()["response"] is None
    # A current but changed policy must invalidate the exact frozen approval.
    with api[2].state.sessions.begin() as session:
        policy = session.get(CapturePolicyRecord, UUID(body["policy_id"]))
        original_policy = dict(policy.policy)
        policy.policy = {**policy.policy, "max_samples": 999}
    denied = api[0].post("/v1/ingest/semantic-cache/lookup", headers=tenant[1], json=lookup)
    assert not denied.json()["hit"] and "POLICY_CHANGED" in denied.json()["reason"]
    with api[2].state.sessions.begin() as session:
        session.get(CapturePolicyRecord, UUID(body["policy_id"])).policy = original_policy
    store = api[2].state.artifact_store
    original_get = store.get
    revoked = False

    def revoke_during_read(project, ref):
        nonlocal revoked
        result = original_get(project, ref)
        if not revoked:
            revoked = True
            with api[2].state.sessions.begin() as session:
                current = session.get(SemanticCacheNamespace, UUID(ns["id"]))
                current.state, current.active_qualification_id, current.active_approval_hash = (
                    "REVOKED",
                    None,
                    None,
                )
        return result

    monkeypatch.setattr(store, "get", revoke_during_read)
    revoked_result = api[0].post("/v1/ingest/semantic-cache/lookup", headers=tenant[1], json=lookup)
    assert (
        revoked and not revoked_result.json()["hit"] and revoked_result.json()["response"] is None
    )
    monkeypatch.setattr(store, "get", original_get)
    # Restore only the synthetic control row to exercise the following immutable-
    # catalog and explicit HTTP-revocation checks in this owned test.
    with api[2].state.sessions.begin() as session:
        current = session.get(SemanticCacheNamespace, UUID(ns["id"]))
        current.state, current.active_qualification_id, current.active_approval_hash = (
            "ACTIVE",
            UUID(q["id"]),
            activation["activation_hash"],
        )
    assert (
        api[0]
        .post(
            "/v1/ingest/semantic-cache/put",
            headers=tenant[1],
            json={**entry, "query": "new unqualified entry"},
        )
        .status_code
        == 409
    )
    assert (
        api[0]
        .post(path + "/semantic-cache/namespaces/" + ns["id"] + "/revoke", headers=api[1], json={})
        .status_code
        == 200
    )
    assert (
        not api[0]
        .post("/v1/ingest/semantic-cache/lookup", headers=tenant[1], json=lookup)
        .json()["hit"]
    )
    with api[2].state.sessions() as session:
        slug = session.get(Project, UUID(tenant[0])).slug
    deleted = api[0].delete(path + "?confirm_slug=" + slug, headers=api[1])
    assert deleted.status_code == 200, deleted.text
