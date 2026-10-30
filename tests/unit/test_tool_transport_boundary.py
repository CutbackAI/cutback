"""Local-only daemon endpoints and actual-profile validation are explicit boundaries."""

import pytest

from cutback.tool_isolation import local_endpoint, profile_matches


@pytest.mark.parametrize(
    "value",
    [
        "tcp://127.0.0.1:2375",
        "ssh://synthetic-host",
        "npipe:////other/pipe/docker",
        "unix:///tmp/../synthetic",
        "https://example.invalid",
        "unix:///tmp/socket\n",
    ],
)
def test_remote_or_ambiguous_docker_endpoints_cannot_receive_seed_content(value):
    with pytest.raises(ValueError, match="LOCAL_DOCKER_ENDPOINT"):
        local_endpoint(value)


def test_missing_or_malformed_inspection_is_not_proof_of_a_valid_runtime():
    for value in ({}, {"Config": {}, "HostConfig": {}}, {"Config": None, "HostConfig": None}):
        assert not profile_matches(
            value, image_id="sha256:" + "a" * 64, owner="owned", session_id="owned"
        )


def test_cancel_wakes_already_waiting_reader_and_retires_policy_content_without_daemon_io():
    import queue
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from time import monotonic
    from types import SimpleNamespace

    from unit.test_isolated_records import records

    from cutback.tool_isolation import IsolatedToolSession

    engine, policy, *_ = records()
    engine.close()
    entered = threading.Event()

    class ObservedQueue(queue.Queue):
        def get(self, *args, **kwargs):
            entered.set()
            return super().get(*args, **kwargs)

    runtime = IsolatedToolSession.__new__(IsolatedToolSession)
    runtime._policy, runtime._closed, runtime._cleanup_confirmed = policy, False, False
    runtime._cleanup_lock, runtime._lifecycle_lock = threading.Lock(), threading.Lock()
    runtime._stopping, runtime._deadline = threading.Event(), monotonic() + 60
    runtime._queue, runtime._process, runtime._reader_thread = ObservedQueue(maxsize=4), None, None
    runtime._watchdog = SimpleNamespace(cancel=lambda: None)
    runtime._temporary = SimpleNamespace(cleanup=lambda: None)
    with ThreadPoolExecutor(max_workers=1) as requests:
        pending = requests.submit(runtime._read)
        assert entered.wait(1)
        runtime.close()
        with pytest.raises(ConnectionError, match="NO_AUTOMATIC_RETRY"):
            pending.result(timeout=1)
    assert runtime.containment_receipt()["cleanup_confirmed"]
    assert "grants" not in runtime._policy.model_dump()
