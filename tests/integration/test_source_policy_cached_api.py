"""Current retained scope participates in immutable approval without any provider dispatch."""

import json
from datetime import datetime
from uuid import UUID, uuid4

import pytest
from support_policy_cached_program import cached_program

from cutback.core.policy_runtime_reference import PolicyRuntimeReference
from cutback.server.policy_program_db import SourcePolicyProgramRecord

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("requested", [False, True])
def test_current_cached_reference_prepares_idempotently_with_private_hashes_redacted_and_no_calls(
    api, tenant, tmp_path, monkeypatch, requested
):
    base, _, _, _, executor, calls, _, row, request, _ = cached_program(
        api, tenant, tmp_path, monkeypatch, requested=requested
    )
    try:
        assert calls == [] and row["status"] == "READY_FOR_APPROVAL"
        assert row["economic_envelope"]["reference_runtime_cache_requested"] is requested
        repeated = api[0].post(base + "/source-policy-programs", headers=api[1], json=request)
        assert repeated.status_code == 201 and repeated.json()["id"] == row["id"]
        public = api[0].get(base + "/source-policy-programs/" + row["id"], headers=tenant[2])
        assert public.status_code == 200 and "OWNED_PRIVATE" not in public.text
        assert "policy_sha256" not in public.text and "payload_id" not in public.text
        assert "summary_sha256" not in json.dumps(
            public.json()["specification"]["runtime_reference"]
        )
        assert public.json()["specification"]["runtime_reference"]["cache_requested"] is requested
        runtime = PolicyRuntimeReference.model_validate(request["runtime_reference"])
        assert (
            datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00")) <= runtime.expires_at
        )
        assert (
            api[0]
            .post(base + "/source-policy-programs", headers=tenant[2], json=request)
            .status_code
            == 403
        )
        assert (
            api[0]
            .post(base + "/source-policy-programs", headers=tenant[1], json=request)
            .status_code
            == 403
        )
    finally:
        executor.close()


def test_late_retained_metadata_invalidates_approval_without_replaying_or_spending(
    api, tenant, tmp_path, monkeypatch
):
    base, _, _, _, executor, calls, _, row, request, body = cached_program(
        api, tenant, tmp_path, monkeypatch
    )
    try:
        response = api[0].post(
            "/v1/ingest/policy-runtime",
            headers=tenant[1],
            json={"project_id": tenant[0], "events": [{**body, "event_id": str(uuid4())}]},
        )
        assert response.status_code == 200
        response = api[0].post(
            base + "/source-policy-programs/" + row["id"] + "/approve",
            headers=api[1],
            json={"program_hash": row["program_hash"], "approval_ref": "owned-cached-current-test"},
        )
        assert response.status_code == 409 and calls == []
        assert response.json()["code"] == "POLICY_PROGRAM_CURRENT_AUTHORITY_OR_INPUTS_INVALID"
        with api[2].state.sessions() as session:
            parent = session.get(SourcePolicyProgramRecord, UUID(row["id"]))
            assert (
                parent.approval_status == "pending"
                and parent.requests_used == 0
                and parent.reserved_usd == 0
            )
        retry = api[0].post(base + "/source-policy-programs", headers=api[1], json=request)
        assert retry.status_code == 409 and calls == []
    finally:
        executor.close()
