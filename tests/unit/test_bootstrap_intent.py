"""Invalid or incompatible bootstrap state never creates replacement authority."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from cutback.agent_bootstrap import binding, bootstrap_connection, validate_connection
from cutback.core.bootstrap import BOOTSTRAP_CONTRACT
from cutback.core.request_profiles import fingerprint
from cutback.core.workspace import private_directory, read_private, write_private


class MetadataOnlyCollector:
    endpoint = "http://127.0.0.1:8080"

    def __init__(self):
        self.calls = []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path))
        assert method == "GET", "UNSUPPORTED_COLLECTOR_RECEIVED_A_WRITE"
        return {"status": "ready"}


def test_old_collector_is_refused_before_credentials_or_project_writes(tmp_path):
    control = MetadataOnlyCollector()
    with pytest.raises(ValueError, match="UPGRADE_REQUIRED"):
        bootstrap_connection(tmp_path, control, owner=str(uuid4()))
    assert control.calls == [("GET", "/v1/health/ready")]
    assert not (tmp_path / ".cutback/agent/bootstrap-credentials.json").exists()


@pytest.mark.parametrize("value", [None, {}, [], False])
def test_malformed_private_intent_is_not_treated_as_a_new_request(tmp_path, value):
    path = private_directory(tmp_path, ".cutback/agent") / "bootstrap-credentials.json"
    write_private(path, value)
    control = MetadataOnlyCollector()
    with pytest.raises(ValueError, match="PRIVATE_BOOTSTRAP_INTENT_INVALID"):
        bootstrap_connection(tmp_path, control, owner=str(uuid4()))
    assert not control.calls


@pytest.mark.parametrize("damage", ["owner", "root", "expiry", "malformed_expiry", "protocol"])
def test_completed_connection_rechecks_binding_and_authority_without_renewing(tmp_path, damage):
    owner = str(uuid4())
    value = {
        "bootstrap_contract": BOOTSTRAP_CONTRACT,
        "bootstrap_receipt": {
            "owner": owner,
            "root_fingerprint": fingerprint(str(tmp_path.resolve())),
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    }
    if damage == "owner":
        value["bootstrap_receipt"]["owner"] = str(uuid4())
    elif damage == "root":
        value["bootstrap_receipt"]["root_fingerprint"] = "0" * 64
    elif damage == "expiry":
        value["bootstrap_receipt"]["expires_at"] = (
            datetime.now(UTC) - timedelta(seconds=1)
        ).isoformat()
    elif damage == "malformed_expiry":
        value["bootstrap_receipt"]["expires_at"] = "SYNTHETIC_PRIVATE_RESPONSE_CANARY"
    else:
        value["bootstrap_contract"] = "unknown.future.contract"
    with pytest.raises(ValueError) as caught:
        validate_connection(tmp_path, value, owner=owner)
    assert "SYNTHETIC_PRIVATE_RESPONSE_CANARY" not in str(caught.value)


def test_known_legacy_connection_metadata_is_not_reinterpreted(tmp_path):
    validate_connection(tmp_path, {"endpoint": "http://127.0.0.1:8080", "project": str(uuid4())})


@pytest.mark.parametrize("damage", ["owner", "endpoint", "copied_root", "expiry", "token"])
def test_pending_intent_cannot_move_or_refresh_authority_before_network(tmp_path, damage):
    class InterruptedCollector:
        endpoint = "http://127.0.0.1:8080"

        def __init__(self):
            self.calls = []

        def __call__(self, method, path, body=None):
            self.calls.append((method, path))
            if method == "GET":
                return {"bootstrap_contract": BOOTSTRAP_CONTRACT}
            raise ValueError("SYNTHETIC_RESPONSE_UNCERTAIN")

    source = tmp_path / "source"
    source.mkdir()
    owner = str(uuid4())
    control = InterruptedCollector()
    with pytest.raises(ValueError, match="RESUME_SAVED_INTENT"):
        bootstrap_connection(source, control, owner=owner)
    journal = source / ".cutback/agent/bootstrap-credentials.json"
    state = read_private(journal)
    root = source
    if damage == "owner":
        owner = str(uuid4())
    elif damage == "endpoint":
        control.endpoint = "http://127.0.0.1:8099"
    elif damage == "copied_root":
        root = tmp_path / "copied"
        root.mkdir()
        journal = private_directory(root, ".cutback/agent") / "bootstrap-credentials.json"
        write_private(journal, state)
    elif damage == "expiry":
        state["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        state["intent_sha256"] = binding(state)
        write_private(journal, state)
    else:
        state["keys"]["ingest"]["token"] = "SYNTHETIC_PRIVATE_CREDENTIAL_CANARY" + "x" * 30
        write_private(journal, state)
    saved = journal.read_bytes()
    control.calls.clear()
    with pytest.raises(ValueError) as caught:
        bootstrap_connection(root, control, owner=owner)
    assert not control.calls
    assert "SYNTHETIC_PRIVATE_CREDENTIAL_CANARY" not in str(caught.value)
    intent_unchanged = journal.read_bytes() == saved
    assert intent_unchanged, "REJECTED_BOOTSTRAP_INTENT_WAS_REPLACED"


def test_key_receipt_fingerprint_is_bound_to_its_project():
    import secrets

    from cutback.core.bootstrap import ScopedKeyInput, scoped_key_fingerprint

    body = ScopedKeyInput(
        request_id=uuid4(),
        role="ingest",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        token=secrets.token_urlsafe(48),
    )
    project_a, project_b = uuid4(), uuid4()
    different = scoped_key_fingerprint(body, project_id=project_a) != scoped_key_fingerprint(
        body, project_id=project_b
    )
    assert different, "SCOPED_KEY_RECEIPT_WAS_NOT_BOUND_TO_ITS_PROJECT"
