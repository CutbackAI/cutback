"""Private setup identities are stable and cannot grant paid retries or renewed rights."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from cutback.agent_preparation import begin, create, require_protocol
from cutback.core.preparation import PREPARATION_CONTRACT
from cutback.core.workspace import private_directory


def state():
    return {"phase": "ANALYZED", "analysis_sha256": "a" * 64, "revision": 0, "checkpoints": []}


def test_setup_ids_and_expiry_do_not_change_on_resume(tmp_path):
    directory = private_directory(tmp_path, "journal")
    saved = state()
    initial = begin(directory, saved, "native")
    assert initial["version"] == PREPARATION_CONTRACT
    assert all(UUID(value).version == 4 for value in initial["requests"].values())
    assert len(set(initial["requests"].values())) == 4
    revision = saved["revision"]
    saved["phase"] = "PREPARING"
    assert begin(directory, saved, "native") == initial
    assert saved["revision"] == revision
    saved["analysis_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="INTENT_CHANGED_REANALYZE"):
        begin(directory, saved, "native")


def test_expired_rights_require_review_instead_of_renewal(tmp_path):
    directory = private_directory(tmp_path, "journal")
    saved = state()
    initial = begin(directory, saved, "native")
    initial["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="RIGHTS_EXPIRED_NEW_REVIEW_REQUIRED"):
        begin(directory, saved, "native")
    assert datetime.fromisoformat(initial["expires_at"]) < datetime.now(UTC)


def test_legacy_completed_metadata_is_preserved_and_uncertain_legacy_setup_abstains(tmp_path):
    directory = private_directory(tmp_path, "journal")
    saved = {**state(), "phase": "PLAN_READY"}
    assert begin(directory, saved, "native") is None
    assert "preparation_intent" not in saved
    saved["phase"] = "PREPARING"
    with pytest.raises(ValueError, match="LEGACY_PREPARATION_REQUIRES_OPERATOR_RECONCILIATION"):
        begin(directory, saved, "native")


def test_old_protocol_and_wrong_returned_identity_cannot_acknowledge_setup(tmp_path):
    class OldCollector:
        def __call__(self, method, path, body=None):
            assert method == "GET" and path == "/v1/health/ready"
            return {"status": "ready"}

    with pytest.raises(ValueError, match="UPGRADE_REQUIRED"):
        require_protocol(OldCollector())

    class IncorrectIdentity:
        def __call__(self, method, path, body=None):
            return {"id": str(uuid4())}

    with pytest.raises(ValueError, match="IDENTITY_MISMATCH"):
        create(
            IncorrectIdentity(),
            "/v1/projects/owned",
            "datasets",
            {},
            {"requests": {"dataset": str(uuid4())}},
            state=state(),
            directory=private_directory(tmp_path, "journal"),
        )


@pytest.mark.parametrize("observed", [None, 7, "SYNTHETIC_PRIVATE_RESPONSE_CANARY"])
def test_untrusted_conflict_receipts_never_enter_the_metadata_journal(tmp_path, observed):
    import json

    from cutback.core.workspace import read_private

    class InvalidReceipt:
        def __call__(self, method, path, body=None):
            return {"id": observed, "token": "SYNTHETIC_PRIVATE_RESPONSE_CANARY"}

    directory = private_directory(tmp_path, "journal")
    with pytest.raises(ValueError, match="IDENTITY_MISMATCH"):
        create(
            InvalidReceipt(),
            "/v1/projects/owned",
            "datasets",
            {},
            {"requests": {"dataset": str(uuid4())}},
            state=state(),
            directory=directory,
        )
    saved = read_private(directory / "session.json")
    assert saved["preparation_conflict"]["observed_request_id"] is None
    assert "SYNTHETIC_PRIVATE_RESPONSE_CANARY" not in json.dumps(saved)
