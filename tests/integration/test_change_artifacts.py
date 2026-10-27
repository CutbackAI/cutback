import hashlib
from uuid import UUID

import pytest
from support_evaluation import SyntheticExecutor, authorized_dataset, plan_for

from cutback.server.db import PayloadSample
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


def test_explicit_patch_content_is_encrypted_scoped_and_revocable(
    api, tenant, tmp_path, monkeypatch
):
    client, admin, app = api
    base, store, dataset, _ = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    spec = plan_for({"immutable_hash": dataset["immutable_hash"]})
    frozen = client.post(
        base + "/experiments/plans",
        headers=admin,
        json={"dataset_id": dataset["id"], "specification": spec.model_dump(mode="json")},
    ).json()
    client.post(
        base + f"/experiments/plans/{frozen['id']}/approve",
        headers=admin,
        json={"plan_hash": frozen["plan_hash"], "approval_ref": "synthetic-patch-review"},
    )
    run_id = client.post(
        base + "/experiments", headers=admin, json={"plan_id": frozen["id"]}
    ).json()["id"]
    assert (
        run_one(app.state.sessions, store, executor=SyntheticExecutor(), execution_enabled=True)[
            "status"
        ]
        == "COMPLETED"
    )
    rec = client.get(base + "/recommendations", headers=admin).json()[0]
    path = base + f"/recommendations/{rec['id']}/patches"
    from cutback.scanner import scan

    source = tmp_path / "app.py"
    source.write_text(
        "from openai import OpenAI\nc=OpenAI()\n"
        "c.responses.create(model='gpt-4.1', input='owned fixture')\n"
    )
    inventory = scan(tmp_path)
    assert client.post(base + "/scans", headers=admin, json=inventory).status_code == 201
    diff = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n"
    artifact = {
        "status": "DRY_RUN_ONLY",
        "production_deployed": False,
        "recommendation_id": rec["id"],
        "run_id": run_id,
        "dataset_hash": dataset["immutable_hash"],
        "plan_hash": frozen["plan_hash"],
        "source_file": "app.py",
        "source_sha256": "a" * 64,
        "call_site_id": inventory["call_sites"][0]["call_site_id"],
        "diff": diff,
        "rollback_diff": "owned rollback fixture",
        "diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
    }
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
