"""Capture faults remain incomplete and fixed diagnostics contain no opaque values."""

import pytest

from cutback.demo_workload_portfolio import drain_workload_capture


@pytest.mark.parametrize(
    "health,drained,expected_flushes",
    [
        ({"native_profile_exported": 7, "native_profile_dropped": 1}, False, 1),
        ({"native_profile_exported": 7}, True, 1),
        ({"native_profile_exported": 0}, False, 3),
        ({"native_profile_exported": "owned-opaque-output", "native_profile_dropped": 1}, True, 1),
    ],
)
def test_dropped_missing_or_still_pending_work_never_becomes_complete(
    health, drained, expected_flushes
):
    budgets = []

    class Incomplete:
        def flush(self, seconds):
            budgets.append(seconds)
            return drained

        def health(self):
            return health

    with pytest.raises(ValueError, match="SYNTHETIC_WORKLOAD_CAPTURE_INCOMPLETE") as error:
        drain_workload_capture(Incomplete())
    assert budgets == [5] * expected_flushes
    assert "owned-opaque-output" not in str(error.value)
