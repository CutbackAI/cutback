import pytest

from cutback.server import worker_boot


def test_expiry_operational_log_refuses_private_or_malformed_driver_details(capsys):
    worker_boot.report_encrypted_expiry(
        {
            "encrypted_object_expiry": {
                "status": "FAILED",
                "result": {
                    "backend": "file",
                    "scanned": 2,
                    "deleted": 1,
                    "failures": 1,
                    "cycle_complete": False,
                    "private_reference": "PRIVATE_SYNTHETIC_OBJECT",
                },
            }
        }
    )
    output = capsys.readouterr().out
    assert '"scanned": 2' in output and '"failures": 1' in output
    assert "PRIVATE" not in output and "private_reference" not in output
    worker_boot.report_encrypted_expiry(
        {
            "encrypted_object_expiry": {
                "status": "PRIVATE_SYNTHETIC_STATUS",
                "result": {
                    "backend": "PRIVATE_SYNTHETIC_BACKEND",
                    "scanned": True,
                    "deleted": 999999,
                    "failures": "PRIVATE_SYNTHETIC_FAILURE",
                    "cycle_complete": "PRIVATE",
                },
            }
        }
    )
    output = capsys.readouterr().out
    assert "PRIVATE" not in output and '"scanned": null' in output
    assert '"status": "FAILED"' in output


def test_encrypted_expiry_is_independent_from_hourly_maintenance_and_inference(monkeypatch):
    monkeypatch.setattr(worker_boot, "configured_execution_enabled", lambda: False)
    calls = []

    class Store:
        def expire_page(self):
            calls.append("bounded-expiry")
            return {"failures": 0, "cycle_complete": False}

    result = worker_boot.worker_cycle(None, Store(), maintenance_due=False)
    assert calls == ["bounded-expiry"] and result["encrypted_object_expiry"]["status"] == "OK"
    worker_boot.worker_cycle(None, Store(), maintenance_due=False, encrypted_expiry_due=False)
    assert calls == ["bounded-expiry"]
    unsupported = worker_boot.worker_cycle(None, object(), maintenance_due=False)
    assert unsupported["encrypted_object_expiry"]["status"] == "UNSUPPORTED"


@pytest.mark.parametrize(
    "phase,delay",
    [
        ({"status": "OK", "result": {"cycle_complete": False}}, 5),
        ({"status": "OK", "result": {"cycle_complete": True}}, 3600),
        ({"status": "FAILED", "result": {"cycle_complete": True}}, 60),
        ({"status": "UNSUPPORTED"}, 3600),
        ({"status": "OK", "result": None}, 60),
        ({"status": "OK", "result": {"cycle_complete": "private"}}, 60),
        (None, 60),
    ],
)
def test_expiry_schedule_retains_failure_backoff_and_refuses_unknown_completion(phase, delay):
    assert worker_boot.encrypted_expiry_delay(phase) == delay


def test_malformed_driver_log_fields_cannot_crash_worker(capsys):
    worker_boot.report_encrypted_expiry(
        {
            "encrypted_object_expiry": {
                "status": {},
                "result": {"backend": {}, "scanned": [], "deleted": {}, "failures": []},
            }
        }
    )
    assert '"status": "FAILED"' in capsys.readouterr().out


def test_encrypted_expiry_failure_is_sanitized_and_does_not_starve_jobs(monkeypatch, capsys):
    monkeypatch.setattr(worker_boot, "configured_execution_enabled", lambda: True)
    monkeypatch.setattr(worker_boot, "run_one", lambda *a, **k: "eligible-job")

    class Store:
        def expire_page(self):
            raise OSError("synthetic private filename")

    result = worker_boot.worker_cycle(None, Store(), maintenance_due=False)
    assert result["encrypted_object_expiry"]["status"] == "FAILED"
    assert result["experiments"] == {"status": "OK", "result": "eligible-job"}
    assert "private filename" not in capsys.readouterr().out


@pytest.mark.parametrize("close_fails", [False, True])
def test_worker_advances_expiry_pages_without_repeating_hourly_maintenance(
    monkeypatch, capsys, close_fails
):
    clock = [0.0]
    calls, closed = [], []

    class Store:
        def close(self):
            closed.append("store")
            if close_fails:
                raise OSError("PRIVATE_SYNTHETIC_CLOSE_DETAIL")

    class Engine:
        def dispose(self):
            closed.append("database")

    store = Store()
    monkeypatch.setenv("CUTBACK_DATABASE_URL", "owned-synthetic-not-contacted")
    monkeypatch.setenv("CUTBACK_PAYLOAD_KEY", "owned-synthetic-not-used")
    monkeypatch.setattr(worker_boot, "database", lambda value: (Engine(), None))
    monkeypatch.setattr("cutback.evals.artifacts.configured_store", lambda: store)
    monkeypatch.setattr(worker_boot.time, "monotonic", lambda: clock[0])

    def cycle(sessions, actual_store, **flags):
        assert actual_store is store
        calls.append(flags)
        if flags["encrypted_expiry_due"]:
            return {
                "encrypted_object_expiry": {
                    "status": "OK",
                    "result": {"cycle_complete": len(calls) > 1},
                }
            }
        return {}

    def sleep(seconds):
        assert seconds == 2
        clock[0] += seconds
        if len(calls) == 5:
            raise KeyboardInterrupt

    monkeypatch.setattr(worker_boot, "worker_cycle", cycle)
    monkeypatch.setattr(worker_boot.time, "sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        worker_boot.main()
    assert [item["maintenance_due"] for item in calls] == [True, False, False, False, False]
    assert [item["encrypted_expiry_due"] for item in calls] == [True, False, False, True, False]
    assert closed == ["store", "database"]
    assert "PRIVATE_SYNTHETIC_CLOSE_DETAIL" not in capsys.readouterr().out


def test_tool_containment_needs_no_content_or_inference_and_is_independently_opted_in(monkeypatch):
    monkeypatch.setattr(worker_boot, "configured_execution_enabled", lambda: False)
    monkeypatch.setenv("CUTBACK_TOOL_EXECUTION", "false")
    monkeypatch.setenv("CUTBACK_TOOL_CONTAINMENT", "false")
    monkeypatch.setattr(
        "cutback.server.execution_ledger.recover_execution_leases", lambda *args: None
    )
    monkeypatch.setattr("cutback.server.campaign_retention.expire_campaigns", lambda *args: None)
    monkeypatch.setattr(worker_boot, "maintain_all", lambda *args, **kwargs: None)
    assert "tool_containment" not in worker_boot.worker_cycle(None, None)
    calls = []
    monkeypatch.setattr(
        "cutback.server.tool_recovery.recover_tool_one",
        lambda sessions: calls.append("containment-only"),
    )
    monkeypatch.setenv("CUTBACK_TOOL_CONTAINMENT", "true")
    assert worker_boot.worker_cycle(None, None)["tool_containment"]["status"] == "OK"
    assert calls == ["containment-only"]


def test_outcome_observation_needs_no_content_or_model_execution(monkeypatch):
    monkeypatch.setattr(worker_boot, "configured_execution_enabled", lambda: False)
    monkeypatch.setenv("CUTBACK_ONLINE_EXECUTION", "false")
    monkeypatch.setenv("CUTBACK_QUALITY_EXECUTION", "false")
    monkeypatch.setenv("CUTBACK_OUTCOME_WATCH_EXECUTION", "false")
    assert "outcome_watches" not in worker_boot.worker_cycle(None, None, maintenance_due=False)
    calls = []
    monkeypatch.setenv("CUTBACK_OUTCOME_WATCH_EXECUTION", "true")
    monkeypatch.setattr(
        "cutback.server.outcome_watches.run_outcome_watch_one",
        lambda sessions, *, enabled: calls.append(enabled),
    )
    result = worker_boot.worker_cycle(None, None, maintenance_due=False)
    assert calls == [True] and result["outcome_watches"]["status"] == "OK"


def test_failed_retention_and_schedule_cannot_starve_experiments(monkeypatch, capsys):
    calls = []

    def unavailable(*args, **kwargs):
        raise ValueError("synthetic private details must be suppressed")

    monkeypatch.setattr(worker_boot, "maintain_all", unavailable)
    monkeypatch.setattr(worker_boot, "expire_payloads", unavailable)
    monkeypatch.setattr("cutback.server.cache_api.expire_cache", lambda *a: None)
    monkeypatch.setattr("cutback.server.training_worker.expire_training_artifacts", lambda *a: None)
    monkeypatch.setattr("cutback.server.continuous.queue_schedules", unavailable)
    monkeypatch.setattr(worker_boot, "configured_execution_enabled", lambda: True)
    monkeypatch.setattr(worker_boot, "run_one", lambda *a, **kw: calls.append("eligible-job"))
    result = worker_boot.worker_cycle(None, object())
    assert calls == ["eligible-job"]
    assert result["maintenance"]["status"] == "FAILED"
    assert result["experiments"]["status"] == "OK"
    assert "private details" not in capsys.readouterr().out


def test_quality_is_independent_disabled_by_default_and_has_isolated_failures(monkeypatch, capsys):
    monkeypatch.setenv("CUTBACK_QUALITY_EXECUTION", "false")
    monkeypatch.setenv("CUTBACK_ONLINE_EXECUTION", "false")
    monkeypatch.setattr(worker_boot, "configured_execution_enabled", lambda: False)
    assert "quality_evaluations" not in worker_boot.worker_cycle(
        None, object(), maintenance_due=False
    )
    monkeypatch.setenv("CUTBACK_QUALITY_EXECUTION", "true")
    calls = []

    def unavailable(*args, **kwargs):
        raise ValueError("synthetic private source should be suppressed")

    monkeypatch.setattr("cutback.server.quality_service.backfill_quality_one", unavailable)
    monkeypatch.setattr(
        "cutback.server.quality_worker.run_quality_one", lambda *a, **k: calls.append("one")
    )
    result = worker_boot.worker_cycle(None, object(), maintenance_due=False)
    assert calls == ["one"] and result["quality_backfill"]["status"] == "FAILED"
    assert result["quality_evaluations"]["status"] == "OK"
    assert "private source" not in capsys.readouterr().out


def test_semantic_local_work_is_opt_in_and_finalizer_failure_cannot_starve_qualification(
    monkeypatch, capsys
):
    monkeypatch.setattr(worker_boot, "configured_execution_enabled", lambda: False)
    monkeypatch.setenv("CUTBACK_SEMANTIC_CACHE_EXECUTION", "false")
    assert "semantic_qualifications" not in worker_boot.worker_cycle(
        None, object(), maintenance_due=False
    )
    monkeypatch.setenv("CUTBACK_SEMANTIC_CACHE_EXECUTION", "true")
    calls = []

    def unavailable(*args, **kwargs):
        raise OSError("synthetic private object details")

    monkeypatch.setattr(
        "cutback.server.semantic_upload_worker.run_semantic_upload_one", unavailable
    )
    monkeypatch.setattr(
        "cutback.server.semantic_cache_worker.run_semantic_cache_one",
        lambda *args, **kwargs: calls.append("qualified-local-work"),
    )
    result = worker_boot.worker_cycle(None, object(), maintenance_due=False)
    assert result["semantic_dataset_uploads"]["status"] == "FAILED"
    assert result["semantic_qualifications"]["status"] == "OK" and calls == ["qualified-local-work"]
    assert "private object" not in capsys.readouterr().out
