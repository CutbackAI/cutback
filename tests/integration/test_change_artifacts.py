from uuid import UUID

import pytest

from cutback.server.db import PayloadSample
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


def test_explicit_patch_content_is_encrypted_scoped_and_revocable(
    api, tenant, tmp_path, monkeypatch
):
    client, admin, app = api
    import httpx
    from support_agent import native_response, prepare_fresh

    from cutback.agent import advance
    from cutback.changes import model_patch
    from cutback.core.workspace import read_private
    from cutback.evals.executors import OpenAIExecutor

    root, control, state, _ = prepare_fresh(api, tenant, tmp_path, monkeypatch)
    base = f"/v1/projects/{tenant[0]}"
    store = app.state.artifact_store
    queued = advance(
        root, control, plan_hash=state["plan"]["plan_hash"], approval_ref="owned-synthetic-only"
    )
    run_id = queued["run_id"]
    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "synthetic-never-provider")
    executor = OpenAIExecutor(
        http_client=httpx.Client(transport=httpx.MockTransport(native_response))
    )
    try:
        assert (
            run_one(
                app.state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=run_id,
            )["status"]
            == "COMPLETED"
        )
    finally:
        executor.close()
    rec = client.get(base + "/recommendations", headers=admin).json()[0]
    path = base + f"/recommendations/{rec['id']}/patches"
    inventory = read_private(root / ".cutback/agent/inventory.json")
    binding = rec["evidence"]["source_binding"]
    artifact = model_patch(
        root,
        inventory,
        binding["call_site_id"],
        rec,
        expected_source_sha256=binding["source_sha256"],
        proposal_only=True,
    )
    diff = artifact["diff"]
    body = {"approval_ref": "owned-synthetic-patch", "artifact": artifact}
    assert client.post(path, headers=admin, json=body).status_code == 409
    client.post(
        base + f"/recommendations/{rec['id']}/review",
        headers=admin,
        json={"decision": "accepted_for_staging", "reason": "owned-synthetic-patch"},
    )
    assert client.post(path, headers=tenant[2], json=body).status_code == 403
    assert (
        client.post(
            path,
            headers=admin,
            json={**body, "artifact": {**artifact, "source_file": "../private.py"}},
        ).status_code
        == 422
    )
    assert (
        client.post(
            path, headers=admin, json={**body, "artifact": {**artifact, "run_id": str(UUID(int=1))}}
        ).status_code
        == 422
    )
    created = client.post(path, headers=admin, json=body)
    assert created.status_code == 201, created.text
    change_id = created.json()["id"]
    metadata = client.get(base + "/changes", headers=tenant[2]).json()
    assert (
        diff not in str(metadata)
        and metadata[0]["evidence"]["source_verified_by"] == "local_cli_attestation"
    )
    read = base + f"/changes/{change_id}/content"
    assert client.post(read, headers=tenant[2], json={"purpose": "patch-review"}).status_code == 403
    assert client.post(read, headers=admin, json={"purpose": "patch-review"}).json()["diff"] == diff
    with app.state.sessions() as session:
        payload = session.get(PayloadSample, UUID(metadata[0]["evidence"]["payload_id"]))
        assert diff.encode() not in store._path(UUID(tenant[0]), payload.object_ref).read_bytes()
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "false")
    assert client.post(read, headers=admin, json={"purpose": "patch-review"}).status_code == 403
