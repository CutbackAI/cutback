"""Finite agent observation survives lost acknowledgements and rejects altered evidence."""

import copy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from test_campaign_outcomes import reviewed

from cutback import campaign_watches as watches
from cutback.core.request_profiles import fingerprint


class Control:
    endpoint = "http://127.0.0.1:8080"

    def __init__(self, saved, row):
        self.saved, self.row = saved, row
        self.requests = []
        self.lost = False
        self.receipt = None
        self.page = {"items": [], "next_after": None}

    def __call__(self, method, path, body=None):
        self.requests.append((method, path, copy.deepcopy(body)))
        if method == "POST" and "/draft?" in path:
            envelope = {
                "project_id": self.saved["project_id"],
                "environment_id": self.row["environment_id"],
                "policy": {
                    **body,
                    "baseline_sha256": "b" * 64,
                    "source_diff_sha256": self.saved["application_review"]["diff_sha256"],
                    "optimization_run_ids": sorted(run["run_id"] for run in self.row["runs"]),
                },
            }
            self.receipt = {
                "id": self.receipt["id"] if self.receipt else str(uuid4()),
                "environment_id": self.row["environment_id"],
                "specification": envelope,
                "approval_hash": fingerprint(envelope),
            }
            if self.lost:
                self.lost = False
                raise OSError("OWNED_LOST_DRAFT_ACKNOWLEDGEMENT")
            return copy.deepcopy(self.receipt)
        if path.endswith("/windows?limit=20"):
            return copy.deepcopy(self.page)
        return copy.deepcopy(self.receipt)


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    saved, row = reviewed()
    saved.update(project_id=str(uuid4()), request={"baseline_snapshot_id": str(uuid4())})
    row["environment_id"] = str(uuid4())
    control = Control(saved, row)
    monkeypatch.setattr(watches, "current", lambda *_: saved)
    monkeypatch.setattr(watches, "campaign_status", lambda *_: row)
    monkeypatch.setattr(
        watches,
        "declared_change",
        lambda *_: {
            "evidence": {
                "diff_sha256": saved["application_review"]["diff_sha256"],
                "run_id": saved["application_review"]["run_id"],
            }
        },
    )
    now = datetime.now(UTC)
    options = dict(
        change_id=str(uuid4()),
        candidate_release="synthetic-candidate",
        first_window_end=(now + timedelta(hours=1)).isoformat(),
        expires_at=(now + timedelta(days=1)).isoformat(),
        window_seconds=60,
        interval_seconds=3600,
        release_linkage_attested=True,
    )
    return tmp_path, control, options


def test_attestation_is_required_before_files_or_control_calls(tmp_path):
    with pytest.raises(ValueError, match="EXPLICIT_CHANGE"):
        watches.prepare(
            tmp_path,
            None,
            change_id=uuid4(),
            candidate_release="x",
            first_window_end="x",
            expires_at="x",
            window_seconds=60,
            interval_seconds=60,
        )
    assert not list(tmp_path.iterdir())


def test_lost_draft_ack_reuses_exact_request_and_approval(scenario):
    root, control, options = scenario
    control.lost = True
    with pytest.raises(OSError, match="LOST_DRAFT"):
        watches.prepare(root, control, **options)
    first = control.requests[-1][2]
    result = watches.prepare(root, control, **options)
    assert control.requests[-1][2] == first
    assert result["provider_calls"] == 0 and not result["traffic_changes_authorized"]
    digest = result["receipt"]["approval_hash"]
    count = len(control.requests)
    with pytest.raises(ValueError, match="EXACT_OUTCOME"):
        watches.approve(root, control, approval_hash="f" * 64, approval_ref="review")
    assert len(control.requests) == count
    assert (
        watches.approve(root, control, approval_hash=digest, approval_ref="review")["receipt"][
            "approval_hash"
        ]
        == digest
    )
    assert control.requests[-1][2] == {"approval_hash": digest, "approval_ref": "review"}
    control.endpoint = "http://127.0.0.1:8081"
    with pytest.raises(ValueError, match="DESTINATION_CHANGED"):
        watches.status(root, control)


@pytest.mark.parametrize("damage", ["hash", "scope", "intent", "family", "diff", "baseline_hash"])
def test_agent_refuses_changed_frozen_policy(scenario, damage):
    root, control, options = scenario
    watches.prepare(root, control, **options)
    receipt = control.receipt
    policy = receipt["specification"]["policy"]
    if damage == "hash":
        receipt["approval_hash"] = "f" * 64
    else:
        if damage == "scope":
            receipt["specification"]["project_id"] = str(uuid4())
        elif damage == "intent":
            policy["candidate_release"] = "different-candidate"
        elif damage == "family":
            policy["optimization_run_ids"] = [str(uuid4())]
        elif damage == "baseline_hash":
            policy["baseline_sha256"] = "f" * 64
        else:
            policy["source_diff_sha256"] = "f" * 64
        receipt["approval_hash"] = fingerprint(receipt["specification"])
    with pytest.raises(ValueError, match="BINDING_MISMATCH"):
        watches.status(root, control)


@pytest.mark.parametrize(
    "damage", ["hash", "approval", "traffic", "bool_cost", "order", "page", "cursor"]
)
def test_agent_checks_window_integrity_order_and_bounded_page(scenario, damage):
    root, control, options = scenario
    result = watches.prepare(root, control, **options)
    evidence = {
        "approval_hash": result["receipt"]["approval_hash"],
        "window_index": 2,
        "provider_calls": 0,
        "action_authorized": False,
        "traffic_changed": False,
    }
    control.page = {
        "items": [{"evidence": evidence, "evidence_sha256": fingerprint(evidence)}],
        "next_after": None,
    }
    assert len(watches.status(root, control)["windows"]["items"]) == 1
    if damage == "hash":
        control.page["items"][0]["evidence_sha256"] = "f" * 64
    elif damage in {"approval", "traffic", "bool_cost"}:
        field, value = {
            "approval": ("approval_hash", "f" * 64),
            "traffic": ("traffic_changed", True),
            "bool_cost": ("provider_calls", False),
        }[damage]
        evidence[field] = value
        control.page["items"][0]["evidence_sha256"] = fingerprint(evidence)
    elif damage == "order":
        control.page["items"] *= 2
    elif damage == "page":
        control.page["items"] *= 21
    else:
        control.page["next_after"] = True
    with pytest.raises(ValueError, match="BINDING_MISMATCH|PAGE_CONTRACT_CHANGED"):
        watches.status(root, control)


@pytest.mark.parametrize("cursor", [-1, True, "2"])
def test_invalid_cursor_never_reads_control(scenario, cursor):
    root, control, _ = scenario
    with pytest.raises(ValueError, match="INVALID_OUTCOME"):
        watches.status(root, control, after=cursor)
    assert not control.requests
