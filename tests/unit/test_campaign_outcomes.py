"""Outcome linkage refuses missing permission, altered reviews and missing costs."""

import copy
from uuid import uuid4

import pytest

from cutback.campaign_outcomes import declared_change, record_outcomes, reviewed_campaign
from cutback.core.request_profiles import fingerprint


def reviewed():
    campaign, confirmation = str(uuid4()), str(uuid4())
    review = {
        "status": "READY_FOR_PR_REVIEW",
        "campaign_id": campaign,
        "campaign_hash": "a" * 64,
        "run_id": confirmation,
        "plan_hash": "b" * 64,
        "diff_sha256": "c" * 64,
    }
    review["review_sha256"] = fingerprint(review)
    saved = {"campaign_id": campaign, "application_review": review}
    row = {
        "status": "COMPLETED",
        "billing_complete": True,
        "campaign_hash": "a" * 64,
        "requests": 3,
        "runs": [
            {
                "run_id": confirmation if phase == "confirmation" else str(uuid4()),
                "phase": phase,
                "status": "COMPLETED",
                "requests": 1,
                "known_cost_requests": 1,
                "plan_hash": "b" * 64,
            }
            for phase in ("proposal", "validation", "confirmation")
        ],
    }
    return saved, row


def test_later_outcomes_need_explicit_release_attestation_before_network_or_workspace(tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("NETWORK_NOT_PERMITTED")

    with pytest.raises(ValueError, match="EXPLICIT_CHANGE_TO_APPLICATION_RELEASE_ATTESTATION"):
        record_outcomes(tmp_path, forbidden, candidate_snapshot_id=uuid4(), change_id=uuid4())
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("damage", ["review", "campaign", "run", "plan", "family", "usage"])
def test_review_and_retained_canonical_family_are_required(damage):
    saved, row = reviewed()
    original = copy.deepcopy(row)
    review, identifiers = reviewed_campaign(saved, row)
    assert review == saved["application_review"] and len(identifiers) == 3
    assert row == original
    if damage == "review":
        saved["application_review"]["diff_sha256"] = "d" * 64
    elif damage == "campaign":
        row["campaign_hash"] = "d" * 64
    elif damage == "run":
        row["runs"][-1]["run_id"] = str(uuid4())
    elif damage == "plan":
        row["runs"][-1]["plan_hash"] = "d" * 64
    elif damage == "family":
        row["runs"].pop(0)
    else:
        row["runs"][0]["known_cost_requests"] = 0
    with pytest.raises(ValueError, match="REVIEW_REQUIRED|FAMILY|CONFIRMATION"):
        reviewed_campaign(saved, row)


def test_change_lookup_follows_fixed_metadata_pages_and_never_reads_payloads():
    desired = str(uuid4())
    paths = []

    def control(method, path):
        assert method == "GET" and "/content" not in path
        paths.append(path)
        return [{"id": str(uuid4())} for _ in range(100)] if len(paths) == 1 else [{"id": desired}]

    assert declared_change(control, "/v1/projects/owned", desired)["id"] == desired
    assert paths == ["/v1/projects/owned/changes?offset=0", "/v1/projects/owned/changes?offset=100"]
    with pytest.raises(ValueError, match="NOT_FOUND"):
        declared_change(lambda *args: [], "/v1/projects/owned", desired)


@pytest.mark.parametrize(
    "version", ["cutback.improvement-campaign.v2", "cutback.improvement-campaign.v3"]
)
def test_simple_first_outcome_family_includes_static_success_and_three_round_fallback(version):
    saved, row = reviewed()
    row["specification"] = {"version": version}
    row["runs"].pop(0)
    row["requests"] = 2
    assert len(reviewed_campaign(saved, row)[1]) == 2
    for phase in ("validation", "proposal", "validation", "proposal", "validation", "proposal"):
        row["runs"].append({**row["runs"][0], "run_id": str(uuid4()), "phase": phase})
    row["requests"] = 8
    assert len(reviewed_campaign(saved, row)[1]) == 8
    row["runs"][0]["known_cost_requests"] = 0
    with pytest.raises(ValueError, match="COST_FAMILY_CHANGED"):
        reviewed_campaign(saved, row)


def test_unbounded_metadata_pages_abstain():
    with pytest.raises(ValueError, match="PAGE_CONTRACT_CHANGED"):
        declared_change(lambda *args: [{}] * 101, "/v1/projects/owned", str(uuid4()))
