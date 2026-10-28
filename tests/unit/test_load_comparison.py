import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_paired_trial_stops_and_retains_incomplete_cleanup_without_next_tenant(
    monkeypatch, tmp_path, capsys
):
    source = Path(__file__).resolve().parents[2] / "examples/load-qualification/compare.py"
    spec = importlib.util.spec_from_file_location("owned_load_comparison", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    before, after = tmp_path / "before", tmp_path / "after"
    before.mkdir()
    after.mkdir()
    monkeypatch.setattr(module, "status", lambda target: {"status": "READY"})
    monkeypatch.setattr(
        module,
        "configured",
        lambda target: (
            target,
            {
                "endpoint": "http://127.0.0.1:" + ("8199" if target == before else "8198"),
                "admin": "OWNED_PRIVATE_COMPARISON_TOKEN",
            },
        ),
    )
    calls = []

    async def failed(endpoint, token, profile):
        calls.append(endpoint)
        return {
            "status": "FAILED_OR_INCOMPLETE_QUALIFICATION",
            "cleanup_complete": False,
            "aggregate_counts_and_unknown_cost_verified": False,
            "mixed_ingest_workload_ns": None,
            "unique_events_acknowledged": 0,
            "raw_client_request_latencies_ns": {"event_ingest": []},
        }

    monkeypatch.setattr(module, "qualify_local", failed)
    monkeypatch.setattr(
        module.argparse.ArgumentParser,
        "parse_args",
        lambda self: SimpleNamespace(
            before_target=before,
            after_target=after,
            profile=source.with_name("profile.json"),
            execute_local=True,
        ),
    )
    with pytest.raises(SystemExit) as stopped:
        module.main()
    assert stopped.value.code == 1 and len(calls) == 1
    output = json.loads(capsys.readouterr().out)
    receipt = json.loads(Path(output["private_receipt"]).read_text(encoding="utf-8"))
    assert receipt["completed_order"] == ["before"] and not receipt["all_planned_trials_completed"]
    assert not receipt["summaries"]["after"]["all_declared_gates_passed"]
    assert "OWNED_PRIVATE" not in json.dumps(receipt)
    assert len(receipt["runs"]) == 1
