from cutback.server import worker_boot


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
