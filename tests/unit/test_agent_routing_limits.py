"""Oversized permitted inputs cannot create a policy, dataset or billable plan."""

import json

import pytest
from support_retrieval_application import git
from support_routing_application import application

from cutback.agent import analyze, prepare


def test_agent_refuses_case_overflow_before_any_collector_write(tmp_path):
    root = application(tmp_path)
    (root / "tickets.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "id": "owned-" + str(index),
                    "input": "Priority: normal.",
                    "gold": "Normal",
                }
            )
            + "\n"
            for index in range(1025)
        ),
        encoding="utf-8",
    )
    git(root, "add", "tickets.jsonl")
    git(root, "commit", "-m", "Independent oversized input fixture")
    state = analyze(root)

    class NoWrites:
        endpoint = "http://127.0.0.1"

        def __call__(self, *args, **kwargs):
            pytest.fail("Oversized preparation must not contact the collector")

    with pytest.raises(ValueError, match="^DATASET_READ_CASE_BOUND$"):
        prepare(
            root,
            NoWrites(),
            "00000000-0000-4000-8000-000000000001",
            files=["tickets.jsonl"],
            rights_basis="synthetic",
            rights_ref="owned-limit-test",
            max_usd="4",
            max_requests=500,
            minimum=1,
            independent_labels=True,
            learned_routing=True,
            fixture_transport=True,
            workflow=state["routing_producers"][0]["workflow_id"],
            min_test_samples=3,
        )
