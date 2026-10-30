"""One independent HTTP-to-encrypted-store-to-worker-to-serving walkthrough."""

from datetime import UTC, datetime, timedelta


def seed_semantic_cache(app, control, project, *, headers=None, execute=True, activate=True):
    from cutback.evals.datasets import digest
    from cutback.sample_app.semantic_cache_fixture import EMBEDDING, cases, qualification
    from cutback.server.semantic_cache_worker import run_semantic_cache_one
    from cutback.server.semantic_upload_worker import run_semantic_upload_one

    base = f"/v1/projects/{project}"
    supplied = {"headers": headers} if headers else {}

    def post(path, body, *, ingest=None):
        response = control.post(
            base + path if not ingest else path,
            json=body,
            **({"headers": {"Authorization": "Bearer " + ingest}} if ingest else supplied),
        )
        if response.is_error:
            raise ValueError("OWNED_SEMANTIC_CACHE_CONTROL_REFUSED_" + str(response.status_code))
        return response.json()

    policy = post(
        "/capture-policies",
        {
            "enabled": True,
            "approval_ref": "owned-semantic-controls",
            "rights_basis": "synthetic",
            "environments": ["staging"],
            "workflows": ["semantic-cache-controls"],
            "allowed_providers": ["local"],
            "sample_rate": 1,
            "max_samples": 600,
            "max_payload_bytes": 65536,
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    key = post("/keys", {"role": "ingest"})
    namespace = post(
        "/semantic-cache/namespaces",
        {
            "policy_id": policy["id"],
            "serving_key_id": key["key_id"],
            "workflow": "semantic-cache-controls",
            "environment": "staging",
            "provider": "local",
            "model": "owned-fallback-reference",
            "prompt_revision": "fixture-v1",
            "freshness_ref": "catalog-v1",
            "access_context_sha256": digest({"context": "owned-fixture-only"}),
            "rule": {"embedding": EMBEDDING, "threshold": 0.5},
            "max_entries": 16,
            "expires_at": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
            "curated_catalog_approved": True,
            "encrypted_storage_approved": True,
        },
    )
    serving = {
        "project_id": project,
        "namespace_id": namespace["id"],
        "access_context_sha256": namespace["specification"]["access_context_sha256"],
        "embedding_contract_sha256": namespace["embedding_contract_sha256"],
        "vector": [1.0, 0.0],
    }
    post(
        "/v1/ingest/semantic-cache/put",
        {
            **serving,
            "query": "Owned curated billing training question",
            "response": "billing",
            "ttl_seconds": 7200,
        },
        ingest=key["token"],
    )
    controls = [case.model_dump(mode="json") for case in cases()]
    prepared = post(
        "/semantic-cache/namespaces/" + namespace["id"] + "/dataset-uploads",
        {
            "metadata": {
                "workflow": "semantic-cache-controls",
                "environment": "staging",
                "policy_id": policy["id"],
                "source_type": "synthetic_fixture",
                "independent_labels": True,
                "representative": False,
                "provenance_ref": "owned-symbolic-vector-controls-v1",
                "split_policy": "task-aware-v2",
            },
            "expected_cases": len(controls),
            "expected_cases_sha256": digest(controls),
            "content_upload_approved": True,
            "max_finalize_seconds": 120,
        },
    )
    route = "/semantic-cache/dataset-uploads/" + prepared["id"]
    for offset in range(0, len(controls), 8):
        post(
            route + "/append",
            {
                "approval_hash": prepared["approval_hash"],
                "offset": offset,
                "cases": controls[offset : offset + 8],
            },
        )
    post(route + "/finalize", {"approval_hash": prepared["approval_hash"]})
    completed = run_semantic_upload_one(
        app.state.sessions,
        app.state.artifact_store,
        execution_enabled=True,
        only_upload_id=prepared["id"],
    )
    if completed["status"] != "FINALIZED":
        raise ValueError("OWNED_SEMANTIC_CACHE_DATASET_DID_NOT_FINALIZE")
    q = post(
        "/semantic-cache/namespaces/" + namespace["id"] + "/qualifications",
        {
            "dataset_id": completed["dataset_id"],
            "specification": qualification(namespace["specification"]["rule"]),
            "max_wall_seconds": 120,
        },
    )
    result = {
        "scenario": "owned-symbolic-semantic-cache.v1",
        "namespace_id": namespace["id"],
        "qualification_id": q["id"],
        "dataset_id": completed["dataset_id"],
        "paid_provider_calls": 0,
        "realized_savings_usd": None,
        "disclosure": (
            "Synthetic vector controls exercise mechanics, not real embedding quality, "
            "customer utility or population independence."
        ),
    }
    if not execute:
        return {**result, "status": "PENDING"}
    route = "/semantic-cache/qualifications/" + q["id"]
    post(
        route + "/approve",
        {"approval_hash": q["approval_hash"], "approval_ref": "owned-local-gates"},
    )
    for _ in range(2):
        run_semantic_cache_one(
            app.state.sessions,
            app.state.artifact_store,
            execution_enabled=True,
            only_qualification_id=q["id"],
        )
    response = control.get(base + route, **supplied)
    if response.is_error or response.json()["state"] != "PASSED":
        raise ValueError("OWNED_SEMANTIC_CACHE_GATES_DID_NOT_PASS")
    receipt = response.json()
    activation = control.get(base + route + "/activation", **supplied).json()
    if not activation["eligible"]:
        raise ValueError("OWNED_SEMANTIC_CACHE_ACTIVATION_DENIED")
    if not activate:
        return {
            **result,
            "status": receipt["state"],
            "report": receipt["report"],
            "selected_threshold": receipt["selected_threshold"],
        }
    post(
        route + "/activate",
        {"approval_hash": activation["activation_hash"], "approval_ref": "owned-curated-demo"},
    )
    hit = post("/v1/ingest/semantic-cache/lookup", serving, ingest=key["token"])
    miss = post(
        "/v1/ingest/semantic-cache/lookup", {**serving, "vector": [0.8, 0.6]}, ingest=key["token"]
    )
    if not hit["hit"] or miss["hit"] or miss["response"] is not None:
        raise ValueError("OWNED_SEMANTIC_CACHE_SERVING_CHECK_FAILED")
    # No credential or raw catalog/query content enters the public sample manifest.
    return {
        **result,
        "status": receipt["state"],
        "selected_threshold": receipt["selected_threshold"],
        "report": receipt["report"],
        "served_authorized_hit": hit["hit"],
        "rejected_unsafe_match": not miss["hit"],
    }
