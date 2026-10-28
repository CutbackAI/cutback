from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_campaign_workflow import advance, approve, prepare

from cutback.server.campaign_db import CampaignCandidate, CampaignRecord

pytestmark = pytest.mark.postgres


def test_candidate_content_requires_current_admin_purpose_policy_and_parent_scope(
    api, tenant, tmp_path, monkeypatch
):
    base, store, row, request = prepare(api, tenant, tmp_path, monkeypatch)
    approve(api, base, row)
    assert advance(api, store, row)["status"] == "WAITING_PROPOSAL"
    with api[2].state.sessions() as session:
        candidate = session.scalar(
            select(CampaignCandidate).where(CampaignCandidate.campaign_id == UUID(row["id"]))
        )
        candidate_id = candidate.id
        policy_id = session.get(CampaignRecord, UUID(row["id"])).policy_id
    endpoint = base + f"/campaigns/{row['id']}/candidates/{candidate_id}/content"
    assert (
        api[0].post(endpoint, headers=tenant[2], json={"purpose": "denied-viewer"}).status_code
        == 403
    )
    assert api[0].post(endpoint, headers=api[1], json={}).status_code == 422
    read = api[0].post(endpoint, headers=api[1], json={"purpose": "synthetic-review"})
    assert read.status_code == 200, read.text
    assert read.json()["configuration"]["instruction"] == "Return A."
    assert "Return A." not in api[0].get(base + f"/campaigns/{row['id']}", headers=tenant[2]).text
    request["request_id"] = str(uuid4())
    other = api[0].post(base + "/campaigns", headers=api[1], json=request)
    assert other.status_code == 201, other.text
    assert (
        api[0]
        .post(
            base + f"/campaigns/{other.json()['id']}/candidates/{candidate_id}/content",
            headers=api[1],
            json={"purpose": "wrong-parent"},
        )
        .status_code
        == 404
    )
    assert api[0].delete(base + f"/capture-policies/{policy_id}", headers=api[1]).status_code == 200
    denied = api[0].post(endpoint, headers=api[1], json={"purpose": "revoked-policy"})
    assert denied.status_code == 409 and "Return A." not in denied.text
