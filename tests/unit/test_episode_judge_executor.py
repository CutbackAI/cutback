"""Native structured episode judges share the target ledger; no provider traffic."""

import json
from decimal import Decimal

import httpx
import pytest
from support_episode_judge import with_semantic_judge
from support_native_agent import native_executor, response_body
from test_episode_runner import episode_case, episode_plan

from cutback.evals.experiments import ExperimentPlan, plan_summary, run
from cutback.server.plan_artifacts import public_config


def semantic_plan(**changes):
    return with_semantic_judge(episode_plan(), **changes)


def semantic_transport(monkeypatch, mode="pass"):
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        if body.get("text", {}).get("format", {}).get("name") == "cutback_episode_judgments":
            assert body["store"] is False and body["text"]["format"]["strict"] is True
            scope = json.loads(body["input"])
            assert scope["criteria"][0]["permitted_source_ids"] == ["reference:0"]
            assert "fixtures" not in scope and "assertions" not in scope
            if mode == "outage":
                return httpx.Response(503, json={"error": {"message": "PRIVATE_PROVIDER_CANARY"}})
            payload = response_body("responses.create", "final")
            payload["output"][0]["content"][0]["text"] = json.dumps(
                {
                    "checks": [
                        {
                            "key": "answer-correctness",
                            "score": 0 if mode == "fail" else 1,
                            "citations": [
                                {
                                    "source_id": "reference:0",
                                    "quote": "invented" if mode == "invalid" else "Urgent",
                                }
                            ],
                        }
                    ]
                }
            )
            if mode == "unknown_usage":
                payload.pop("usage")
            return httpx.Response(200, json=payload)
        tooling = any(item.get("type") == "function_call_output" for item in body["input"])
        return httpx.Response(
            200, json=response_body("responses.create", "final" if tooling else "tool")
        )

    return native_executor("responses.create", monkeypatch, handle), requests


@pytest.mark.parametrize("mode", ["pass", "fail", "invalid", "outage", "unknown_usage"])
def test_native_semantic_proxy_quotes_cost_scope_unknowns_and_no_retry(monkeypatch, caplog, mode):
    plan = semantic_plan()
    dataset = {
        "immutable_hash": "a" * 64,
        "items": [{"case": episode_case(), "group_hash": "owned", "split": "test"}],
    }
    forecast = plan_summary(plan, dataset)
    assert forecast["requests"] == 8
    executor, requests = semantic_transport(monkeypatch, mode)
    try:
        with caplog.at_level("DEBUG"):
            report = run(plan, dataset, executor, approval_hash=forecast["plan_hash"])
        if mode in {"outage", "unknown_usage"}:
            assert len(requests) == 3 and report["status"] == "FAILED"
            assert not report["billing_complete"]
        else:
            assert len(requests) == 6 and report["requests"] == 6
            assert report["status"] == "COMPLETED" and report["billing_complete"]
            for row in report["models"]:
                assert row["requests"] == 3 and row["target_requests"] == 2
                assert row["latency_coverage"] == 1 and len(row["latency_ms"]) == 2
                proxy = row["semantic_judging"]
                assert proxy["judge_requests"] == 1
                assert Decimal(proxy["known_judge_cost_subtotal_usd"]) > 0
                assert row["quality"]["value"] == (
                    1 if mode == "pass" else 0 if mode == "fail" else None
                )
                assert row["quality"]["semantic_quality"] == "NOT VERIFIED"
                assert proxy["criteria"][0]["unknown"] == int(mode == "invalid")
            assert report["recommendations"] == []
        assert "PRIVATE_RUBRIC_CANARY" not in json.dumps(report) + caplog.text
        assert "PRIVATE_PROVIDER_CANARY" not in caplog.text
        assert "PRIVATE_RUBRIC_CANARY" not in json.dumps(
            public_config(plan.model_dump(mode="json"))
        )
    finally:
        executor.close()


def test_v4_hashes_and_new_judge_nonce_and_version_binding():
    old = episode_plan()
    assert "semantic_judging" not in old.model_dump(mode="json")["episode_execution"]
    from cutback.evals.datasets import digest

    assert digest(old.model_dump(mode="json")) == (
        "9cd5dc12199fc09abdf200564fcd2f0e26290eef2048d6ebf8a8a87a9999d361"
    )
    plan = semantic_plan()
    raw = plan.model_dump(mode="json")
    for damage in ("content_nonce", "judge"):
        changed = {**raw, damage: None}
        with pytest.raises(ValueError):
            ExperimentPlan.model_validate(changed)
    raw["evaluator_version"] = "deterministic-v4"
    with pytest.raises(ValueError):
        ExperimentPlan.model_validate(raw)
