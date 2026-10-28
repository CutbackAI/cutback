from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from cutback.core.outcome_watches import OutcomeWatchSpec, due_window, outcome_review_action
from cutback.server.economics_api import TaskSnapshotInput
from cutback.server.traces import fingerprint


def spec(**changes):
    return OutcomeWatchSpec(
        watch_id=uuid4(),
        key="owned-metadata-policy",
        baseline_snapshot_id=uuid4(),
        baseline_sha256="a" * 64,
        change_id=uuid4(),
        source_diff_sha256="b" * 64,
        optimization_run_ids=[uuid4()],
        release_linkage_attested=True,
        candidate_release="owned-candidate",
        window_seconds=60,
        interval_seconds=3600,
        first_window_end=datetime(2026, 10, 28, tzinfo=UTC),
        grace_seconds=300,
        expires_at=datetime(2026, 10, 30, tzinfo=UTC),
        **changes,
    )


def test_closed_window_grace_half_open_clock_edges_and_no_catchup():
    policy = spec()
    first = policy.first_window_end + timedelta(seconds=policy.grace_seconds)
    assert due_window(policy, first - timedelta(microseconds=1), -1) is None
    zero = due_window(policy, first, -1)
    assert zero["index"] == 0 and zero["skipped"] == 0
    assert zero["end"] == policy.first_window_end
    assert due_window(policy, first, 0) is None
    later = due_window(policy, first + timedelta(hours=3, seconds=1), -1)
    assert later["index"] == 3 and later["skipped"] == 3
    assert later["start"] == policy.first_window_end + timedelta(hours=3, seconds=-60)
    assert due_window(policy, policy.expires_at, 0) is None


def test_sparse_or_unknown_outcomes_cannot_recommend_rollback_even_with_observed_difference():
    report = {
        "reasons": ["declared_outcome_regression"],
        "descriptive_outcome_non_regression": False,
    }
    assert outcome_review_action(report) == "REVIEW_ROLLBACK"
    for missing in ("insufficient_declared_task_sample", "outcome_coverage_unknown"):
        assert (
            outcome_review_action({**report, "reasons": [*report["reasons"], missing]})
            == "COLLECT_MISSING_EVIDENCE"
        )


def test_service_filter_omission_preserves_released_snapshot_request_hash():
    value = TaskSnapshotInput(
        snapshot_id=UUID(int=1),
        start=datetime(2026, 10, 1, tzinfo=UTC),
        end=datetime(2026, 10, 2, tzinfo=UTC),
        workflow="owned.task",
        release="owned-v1",
        expected_tasks=2,
    ).model_dump(mode="json")
    assert "service" not in value
    assert (
        fingerprint({"environment_id": str(UUID(int=2)), **value})
        == "403d966563a4f39ae464c0b81fa6dc3681cbe5d4c9b9323b69a225803f627608"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"release_linkage_attested": False},
        {"window_seconds": 60, "interval_seconds": 59},
        {"max_windows": True},
        {"watch_id": UUID(int=0)},
    ],
)
def test_policy_requires_explicit_attestation_strict_bounded_limits_and_nonzero_ids(change):
    raw = spec().model_dump(mode="json")
    with pytest.raises(ValueError):
        OutcomeWatchSpec.model_validate({**raw, **change})
