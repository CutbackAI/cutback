from cutback.server import worker_boot


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
