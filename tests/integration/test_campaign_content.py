from contextlib import contextmanager
from uuid import UUID

import pytest
from sqlalchemy import select
from test_campaign_workflow import prepare

from cutback.server.campaign_content import content_transaction, put_payload, read_payload
from cutback.server.campaign_db import CampaignRecord
from cutback.server.campaign_service import load_campaign
from cutback.server.dataset_lineage import lock_lineage
from cutback.server.db import PayloadSample

pytestmark = pytest.mark.postgres


def test_confirmed_rollback_cleans_only_unreferenced_encrypted_content(
    api, tenant, tmp_path, monkeypatch
):
    _, store, row, _ = prepare(api, tenant, tmp_path, monkeypatch)
    sessions = api[2].state.sessions
    refs = []
    with pytest.raises(ValueError, match="SYNTHETIC_ROLLBACK"):
        with content_transaction(sessions, store, UUID(tenant[0])) as (session, stored):
            lock_lineage(session, UUID(tenant[0]))
            parent = session.get(CampaignRecord, UUID(row["id"]))
            _, policy, _, _ = load_campaign(session, store, parent)
            put_payload(
                session,
                store,
                parent,
                {"instruction": "synthetic-private"},
                "campaign_candidate",
                stored,
                policy,
            )
            refs.extend(stored)
            raise ValueError("SYNTHETIC_ROLLBACK")
    assert len(refs) == 1
    with pytest.raises(FileNotFoundError):
        store.get(UUID(tenant[0]), refs[0])
    with sessions() as session:
        assert (
            session.scalar(select(PayloadSample.id).where(PayloadSample.object_ref == refs[0]))
            is None
        )


def test_lost_commit_acknowledgement_keeps_committed_encrypted_reference(
    api, tenant, tmp_path, monkeypatch
):
    _, store, row, _ = prepare(api, tenant, tmp_path, monkeypatch)
    sessions = api[2].state.sessions

    class LostCommitAck:
        def __call__(self):
            return sessions()

        @contextmanager
        def begin(self):
            with sessions.begin() as session:
                yield session
            raise ValueError("SYNTHETIC_COMMIT_ACK_LOST")

    payload_id = None
    with pytest.raises(ValueError, match="COMMIT_ACK_LOST"):
        with content_transaction(LostCommitAck(), store, UUID(tenant[0])) as (session, refs):
            lock_lineage(session, UUID(tenant[0]))
            parent = session.get(CampaignRecord, UUID(row["id"]))
            _, policy, _, _ = load_campaign(session, store, parent)
            payload = put_payload(
                session,
                store,
                parent,
                {"instruction": "synthetic-private"},
                "campaign_candidate",
                refs,
                policy,
            )
            payload_id = payload.id
    with sessions() as session:
        parent = session.get(CampaignRecord, UUID(row["id"]))
        _, policy, _, _ = load_campaign(session, store, parent)
        value, _ = read_payload(session, store, parent, payload_id, policy)
        assert value == {"instruction": "synthetic-private"}
