"""TRAIN-only native generation, explicit rubric review and separately charged episode judges."""

import json
from uuid import UUID

import pytest
from sqlalchemy import select
from support_episode_judge import semantic_response
from support_evaluation import authorized_dataset, dataset_body
from support_native_agent import native_executor, response_body

from cutback.core.evaluation_criteria import source_evaluator_eligible
from cutback.server.criteria_worker import run_criteria_one
from cutback.server.db import CapturePolicyRecord, DatasetRecord, ExperimentRunRecord
from cutback.server.execution_db import ExecutionCall
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize(
    "mode", ["complete", "invalid_judge", "withdraw_during_target", "generation_outage"]
)
@pytest.mark.parametrize(
    "surface", ["responses.create", "chat.completions.create", "messages.create"]
)
def test_generated_rubrics_freeze_owner_thresholds_and_use_real_separately_reserved_native_judges(
    api, tenant, tmp_path, monkeypatch, mode, surface
):
    client, admin, app = api
    base, store, initial, _ = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    with app.state.sessions() as session:
        policy = session.get(DatasetRecord, UUID(initial["id"])).provenance["policy_id"]
    provider = "anthropic" if surface == "messages.create" else "openai"
    baseline_model = "claude-sonnet-4-6" if provider == "anthropic" else "gpt-4.1"
    candidate_model = "claude-haiku-4-5" if provider == "anthropic" else "gpt-4.1-mini"
    if provider == "anthropic":
        with app.state.sessions.begin() as session:
            captured = session.get(CapturePolicyRecord, UUID(policy))
            captured.policy = {
                **captured.policy,
                "allowed_providers": ["openai", "anthropic", "local"],
            }
    data = dataset_body(30).model_dump(mode="json")
    data["policy_id"] = policy
    data["cases"] = [
        {
            "id": "episode-" + str(i),
            "input": "Owned independent semantic episode " + chr(65 + i),
            "gold": "Urgent",
            "episode": {"max_turns": 1, "assertions": {"accepted_answers": ["Urgent"]}},
        }
        for i in range(30)
    ]
    response = client.post(base + "/datasets", headers=admin, json=data)
    assert response.status_code == 201
    dataset = response.json()
    requirements = {
        "kind": "episode_semantic_rubrics_v1",
        "task_brief": "OWNED_PRIVATE_RUBRIC_TASK",
        "minimum": 0.9,
        "max_regression": 0,
        "min_test_samples": 3,
        "min_test_groups": 3,
        "dimensions": [
            {"kind": "answer_correctness", "requirement": "OWNED_PRIVATE_DIMENSION", "minimum": 1.0}
        ],
        "judge": {"model": "gpt-4.1-mini", "max_input_tokens": 24000, "max_output_tokens": 1024},
    }
    drafted = client.post(
        base + "/criteria/drafts",
        headers=admin,
        json={
            "dataset_id": dataset["id"],
            "requirements": requirements,
            "generator": {
                "key": "generator",
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "max_input_tokens": 24000,
                "max_output_tokens": 2048,
            },
            "budget": {"max_usd": "0.1", "max_requests": 1},
            "transport": "scripted_test_only",
        },
    )
    assert drafted.status_code == 201
    version = drafted.json()
    path = base + "/criteria/" + version["id"]
    assert "OWNED_PRIVATE" not in drafted.text
    assert (
        client.post(
            path + "/approve-generation",
            headers=admin,
            json={"plan_hash": version["plan_hash"], "approval_ref": "owned-generation-budget"},
        ).status_code
        == 200
    )
    queued = client.post(
        path + "/runs", headers=admin, json={"plan_hash": version["plan_hash"]}
    ).json()
    calls = []

    def handler(request):
        import httpx

        body = json.loads(request.content)
        generating = "Propose one evaluation rubric" in body.get("instructions", "")
        judging = body.get("text", {}).get("format", {}).get("name") == "cutback_episode_judgments"
        target_surface = "responses.create" if generating or judging else surface
        expected_url = {
            "responses.create": "https://api.openai.com/v1/responses",
            "chat.completions.create": "https://api.openai.com/v1/chat/completions",
            "messages.create": "https://api.anthropic.com/v1/messages",
        }[target_surface]
        assert str(request.url) == expected_url
        if target_surface != "messages.create":
            assert body.get("store") is False
        calls.append(body)
        response = response_body(target_surface)
        response["model"] = body["model"]
        if "Propose one evaluation rubric" in body.get("instructions", ""):
            if mode == "generation_outage":
                raise httpx.ReadTimeout("OWNED_SYNTHETIC_GENERATION_OUTAGE", request=request)
            frame = json.loads(body["input"])
            assert frame["unseen_test_rows_supplied"] is False
            assert len(frame["training"]) <= 8 and frame["dimensions"][0]["id"] == "dimension-1"
            response["output"][0]["content"][0]["text"] = json.dumps(
                {
                    "criteria": [
                        {
                            "dimension": "dimension-1",
                            "judge_rubric": "OWNED_PRIVATE_GENERATED_RUBRIC",
                            "training_anchors": ["training-1"],
                        }
                    ]
                }
            )
        elif body.get("text", {}).get("format", {}).get("name") == "cutback_episode_judgments":
            response = semantic_response(response, "invalid" if mode == "invalid_judge" else "pass")
            parsed = json.loads(response["output"][0]["content"][0]["text"])
            parsed["checks"][0]["key"] = "semantic-1"
            response["output"][0]["content"][0]["text"] = json.dumps(parsed)
        elif mode == "withdraw_during_target":
            assert (
                client.post(
                    path + "/review",
                    headers=admin,
                    json={
                        "version_sha256": v["version_sha256"],
                        "decision": "reject",
                        "expected_review_approval_id": v["review_approval_id"],
                        "approval_ref": "owned-withdrawal-during-target",
                    },
                ).status_code
                == 200
            )
        if target_surface == "messages.create":
            import httpx2

            return httpx2.Response(200, json=response)
        return httpx.Response(200, json=response)

    executor = native_executor(surface, monkeypatch, handler)
    if provider == "anthropic":
        import httpx

        from cutback.evals.executors import OpenAIExecutor

        monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "owned-never-sent")
        executor.clients["openai"] = OpenAIExecutor(
            http_client=httpx.Client(
                transport=httpx.MockTransport(handler),
                trust_env=False,
            )
        )
    executor.synthetic_transport = True
    try:
        generation = run_criteria_one(
            app.state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=queued["generation_run_id"],
        )
        if mode == "generation_outage":
            assert generation["status"] == "FAILED" and len(calls) == 1
            with app.state.sessions() as session:
                failed = session.get(ExperimentRunRecord, UUID(queued["generation_run_id"]))
                assert failed.requests_used == 1 and failed.known_cost_requests == 0
                assert (
                    failed.reserved_usd > 0
                    and failed.results["operation"] == "criteria-generation-v2"
                )
            run_criteria_one(
                app.state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=queued["generation_run_id"],
            )
            assert len(calls) == 1
            return
        assert generation["status"] == "COMPLETED"
        content = client.post(
            path + "/content", headers=admin, json={"purpose": "owned-private-review"}
        )
        assert content.status_code == 200
        assert content.json()["criteria"][0]["minimum"] == 1.0
        assert (
            client.post(
                path + "/content", headers=tenant[2], json={"purpose": "viewer-negative"}
            ).status_code
            == 403
        )
        reviewed = client.post(
            path + "/review",
            headers=admin,
            json={
                "version_sha256": content.json()["version_sha256"],
                "decision": "approve",
                "approval_ref": "owned-rubric-review",
            },
        )
        assert reviewed.status_code == 200
        v = reviewed.json()
        binding = {
            "criteria_id": v["id"],
            "version_sha256": v["version_sha256"],
            "review_approval_id": v["review_approval_id"],
        }
        planned = client.post(
            path + "/evaluation-draft",
            headers=admin,
            json={
                "dataset_id": dataset["id"],
                "criteria_binding": binding,
                "baseline": {
                    "key": "baseline",
                    "provider": provider,
                    "model": baseline_model,
                    "max_input_tokens": 24000,
                    "max_output_tokens": 1024,
                },
                "candidates": [
                    {
                        "key": "candidate",
                        "provider": provider,
                        "model": candidate_model,
                        "max_input_tokens": 24000,
                        "max_output_tokens": 1024,
                    }
                ],
                "budget": {"max_usd": "3", "max_requests": 100},
                "episode_execution": {
                    "max_turns": 1,
                    "transport": "scripted_test_only",
                    "openai_surface": surface if provider == "openai" else "responses.create",
                },
            },
        )
        assert planned.status_code == 201
        p = planned.json()
        private = client.post(
            base + "/experiments/plans/" + p["id"] + "/content",
            headers=admin,
            json={"purpose": "owned-frozen-threshold-test"},
        ).json()["specification"]
        private["episode_execution"]["semantic_judging"]["criteria"][0]["minimum"] = 0
        assert (
            client.post(
                base + "/experiments/plans",
                headers=admin,
                json={
                    "dataset_id": dataset["id"],
                    "specification": private,
                },
            ).status_code
            == 409
        )
        assert (
            client.post(
                base + "/experiments/plans/" + p["id"] + "/approve",
                headers=admin,
                json={
                    "plan_hash": p["plan_hash"],
                    "approval_ref": "owned-target-and-judge-budget",
                },
            ).status_code
            == 200
        )
        run = client.post(base + "/experiments", headers=admin, json={"plan_id": p["id"]}).json()
        result = run_one(
            app.state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=run["id"],
        )
        if mode == "withdraw_during_target":
            assert result["status"] == "FAILED" and len(calls) == 2
            with app.state.sessions() as session:
                stopped = session.get(ExperimentRunRecord, UUID(run["id"]))
                assert stopped.requests_used == stopped.known_cost_requests == 1
            run_one(
                app.state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=run["id"],
            )
            assert len(calls) == 2
            return
        assert result["status"] == "COMPLETED"
        with app.state.sessions() as session:
            generated = session.get(ExperimentRunRecord, UUID(queued["generation_run_id"]))
            assert generated.results["operation"] == "criteria-generation-v2"
            assert generated.requests_used == generated.known_cost_requests == 1
            records = session.scalars(
                select(ExecutionCall).where(ExecutionCall.run_id == UUID(run["id"]))
            ).all()
            assert {r.purpose for r in records} == {"evaluation", "judge"}
            assert sum(r.purpose == "judge" for r in records) * 2 == len(records)
        report = client.get(base + "/experiments/" + run["id"], headers=admin)
        assert "OWNED_PRIVATE" not in report.text
        recommendation = report.json()["results"]["recommendations"][0]
        assert recommendation["evaluator_version"] == "deterministic-v14"
        assert source_evaluator_eligible(recommendation) is False
        assert recommendation["statistical_gate_passed"] is False
        if mode == "invalid_judge":
            assert recommendation["quality_gate_passed"] is False
            assert all(
                model["quality"]["value"] is None for model in report.json()["results"]["models"]
            )
            return
        assert recommendation["quality_gate_passed"] and recommendation["cheaper_on_sample"]
        confirmation = client.post(
            path + "/confirmation",
            headers=admin,
            json={
                "validation_run_id": run["id"],
                "candidate_key": "candidate",
                "budget": {"max_usd": "3", "max_requests": 100},
            },
        )
        assert confirmation.status_code == 201
        confirmed = confirmation.json()
        assert (
            client.post(
                base + "/experiments/plans/" + confirmed["id"] + "/approve",
                headers=admin,
                json={
                    "plan_hash": confirmed["plan_hash"],
                    "approval_ref": "owned-untouched-proxy-confirmation",
                },
            ).status_code
            == 200
        )
        confirmed_run = client.post(
            base + "/experiments", headers=admin, json={"plan_id": confirmed["id"]}
        ).json()
        assert (
            run_one(
                app.state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=confirmed_run["id"],
            )["status"]
            == "COMPLETED"
        )
        final = client.get(base + "/experiments/" + confirmed_run["id"], headers=admin).json()[
            "results"
        ]
        assert final["recommendations"][0]["semantic_quality"] == "NOT VERIFIED"
        assert final["criteria_confirmation_predecessor_run_id"] == run["id"]
        assert final["criteria_generation_cost"]["included_in_target_evaluation_spend"] is False
    finally:
        executor.close()
