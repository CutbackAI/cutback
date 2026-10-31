"""Offline discovery and failed source-routing stages cannot advance inference."""

import json
from uuid import uuid4

import pytest
from support_retrieval_application import git
from support_routing_application import application

from cutback.agent import advance, analyze, load_session, workspace
from cutback.core.workspace import write_private


def test_routing_discovery_is_offline_private_resumable_and_refuses_unqualified_code(
    tmp_path, monkeypatch
):
    import socket

    root = application(tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("Discovery cannot contact a provider or execute application code")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    state = analyze(root)
    assert state["provider_calls"] == 0 and len(state["routing_producers"]) == 1
    assert "Classify the declared" not in json.dumps(state)
    assert analyze(root)["id"] == state["id"]
    with (root / "app.py").open("a", encoding="utf-8") as stream:
        stream.write('\nraise RuntimeError("NEVER_EXECUTE")\n')
    git(root, "add", "app.py")
    git(root, "commit", "-m", "Independent unqualified wrapper refusal")
    assert not analyze(root)["routing_producers"]


@pytest.mark.parametrize("failure", ["quality", "cost", "billing", "freshness"])
def test_failed_stage_stops_before_fitting_confirmation_or_another_run(tmp_path, failure):
    root = application(tmp_path)
    state = analyze(root)
    _, directory = workspace(root)
    state.update(
        search_mode="source_routing",
        phase="QUEUED",
        endpoint="http://127.0.0.1",
        project_id=str(uuid4()),
        run_id=str(uuid4()),
        routing={"stage": "confirmation" if failure == "freshness" else "validation"},
    )
    write_private(directory / "session.json", state)
    calls = []

    class ReadOnly:
        endpoint = "http://127.0.0.1"

        def __call__(self, method, path, body=None):
            calls.append((method, path))
            assert method == "GET" and path.endswith("/experiments/" + state["run_id"])
            return {
                "id": state["run_id"],
                "status": "COMPLETED",
                "requests": 2,
                "reserved_usd": "1",
                "spent_usd": None if failure == "billing" else "0.1",
                "results": {
                    "billing_complete": failure != "billing",
                    "lineage": {"fresh_test_claim": failure != "freshness"},
                    "recommendations": [
                        {
                            "quality_gate_passed": failure != "quality",
                            "cheaper_on_sample": failure != "cost",
                        }
                    ],
                },
            }

    stopped = advance(root, ReadOnly())
    assert stopped["phase"] == "STOPPED"
    assert stopped["stop_code"] == "ROUTING_STAGE_QUALITY_COST_OR_FRESHNESS_NOT_PASSED"
    assert advance(root, ReadOnly())["phase"] == "STOPPED" and len(calls) == 1
    assert load_session(root)["routing"] == state["routing"]
