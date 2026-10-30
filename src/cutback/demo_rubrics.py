"""Independent reviewed semantic-rubric journey; every native call has an owned transport."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4


def synthetic_executor():
    import httpx

    from cutback.evals.agent_executor import NativeAgentExecutor
    from cutback.evals.executors import OpenAIExecutor

    calls = []

    def handle(request):
        if str(request.url) != "https://api.openai.com/v1/responses":
            raise ValueError("SYNTHETIC_RUBRIC_DESTINATION_REFUSED")
        body = json.loads(request.content)
        if body.get("store") is not False:
            raise ValueError("SYNTHETIC_RUBRIC_STORAGE_POLICY_REFUSED")
        purpose = "target"
        serialized = json.dumps(body["input"])
        answer = "Urgent" if "Priority: urgent." in serialized else "Normal"
        if body["max_output_tokens"] <= 64:
            answer = "Normal"  # Deliberate unsafe fixture, never a claim about the model.
        output = json.dumps({"answer": answer, "citations": []})
        if "Propose one evaluation rubric" in body.get("instructions", ""):
            purpose = "generation"
            frame = json.loads(body["input"])
            if frame.get("unseen_test_rows_supplied") is not False:
                raise ValueError("SYNTHETIC_RUBRIC_TRAINING_ONLY_REQUIRED")
            output = json.dumps(
                {
                    "criteria": [
                        {
                            "dimension": dimension["id"],
                            "judge_rubric": (
                                "Review the reference against the returned answer "
                                "using exact evidence."
                            ),
                            "training_anchors": [frame["training"][0]["anchor"]],
                        }
                        for dimension in frame["dimensions"]
                    ]
                }
            )
        elif body.get("text", {}).get("format", {}).get("name") == "cutback_episode_judgments":
            purpose = "judge"
            frame = json.loads(body["input"])
            sources = {row["source_id"]: row["text"] for row in frame["sources"]}
            output = json.dumps(
                {
                    "checks": [
                        {
                            "key": criterion["key"],
                            "score": float(
                                frame["final_answer"]
                                == sources[criterion["permitted_source_ids"][0]]
                            ),
                            "citations": [
                                {
                                    "source_id": criterion["permitted_source_ids"][0],
                                    "quote": sources[criterion["permitted_source_ids"][0]][:4000],
                                }
                            ],
                        }
                        for criterion in frame["criteria"]
                    ]
                }
            )
        calls.append({"purpose": purpose, "model": body["model"]})
        inputs = len(json.dumps(body["input"]).encode()) + len(
            body.get("instructions", "").encode()
        )
        outputs = len(output.encode())
        return httpx.Response(
            200,
            json={
                "id": "resp_rubric_" + uuid4().hex,
                "object": "response",
                "created_at": 1,
                "model": body["model"],
                "status": "completed",
                "output": [
                    {
                        "id": "msg_owned",
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": output, "annotations": []}],
                    }
                ],
                "usage": {
                    "input_tokens": inputs,
                    "output_tokens": outputs,
                    "total_tokens": inputs + outputs,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    with patch.dict("os.environ", {"CUTBACK_EXPERIMENT_OPENAI_KEY": "synthetic-no-provider-key"}):
        executor = NativeAgentExecutor()
        executor.clients["openai"] = OpenAIExecutor(
            http_client=httpx.Client(
                transport=httpx.MockTransport(handle),
                trust_env=False,
                follow_redirects=False,
            )
        )
    executor.synthetic_transport = True
    return executor, calls


def sample_cases():
    return [
        {
            "id": "rubric-" + str(i),
            "input": "Owned reference ticket "
            + chr(65 + i // 26)
            + chr(65 + i % 26)
            + (". Priority: urgent." if i % 2 else ". Priority: normal."),
            "gold": "Urgent" if i % 2 else "Normal",
            "episode": {
                "max_turns": 1,
                "assertions": {"accepted_answers": ["Urgent" if i % 2 else "Normal"]},
            },
        }
        for i in range(30)
    ]


def seed_rubrics(app, control, project_id, *, execute=True):
    from cutback.server.criteria_worker import run_criteria_one
    from cutback.server.worker import run_one

    base = f"/v1/projects/{project_id}"

    def post(path, body):
        response = control.post(path, json=body)
        if response.is_error:
            raise ValueError("SYNTHETIC_RUBRIC_CONTROL_FAILED_" + str(response.status_code))
        return response.json()

    workflow = "synthetic-reviewed-rubrics"
    policy = post(
        base + "/capture-policies",
        {
            "enabled": True,
            "approval_ref": "owned-synthetic-rubric-demo",
            "rights_basis": "synthetic",
            "environments": ["staging"],
            "workflows": [workflow],
            "allowed_providers": ["openai"],
            "sample_rate": 1,
            "max_samples": 500,
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    dataset = post(
        base + "/datasets",
        {
            "workflow": workflow,
            "policy_id": policy["id"],
            "source_type": "synthetic_fixture",
            "provenance_ref": "independent-reviewed-rubrics-v1",
            "independent_labels": True,
            "cases": sample_cases(),
        },
    )
    draft = post(
        base + "/criteria/drafts",
        {
            "dataset_id": dataset["id"],
            "transport": "scripted_test_only",
            "requirements": {
                "kind": "episode_semantic_rubrics_v1",
                "task_brief": "Assess owned synthetic support answers.",
                "minimum": 0.9,
                "min_test_samples": 3,
                "min_test_groups": 3,
                "dimensions": [
                    {
                        "kind": "answer_correctness",
                        "requirement": "Compare the answer with the declared reference.",
                        "minimum": 1.0,
                    }
                ],
                "judge": {
                    "model": "gpt-4.1-mini",
                    "max_input_tokens": 24000,
                    "max_output_tokens": 1024,
                },
            },
            "generator": {
                "key": "generator",
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "max_input_tokens": 24000,
                "max_output_tokens": 2048,
            },
            "budget": {"max_usd": "0.1", "max_requests": 1},
        },
    )
    state = {
        "criteria_id": draft["id"],
        "dataset_id": dataset["id"],
        "generation_plan_hash": draft["plan_hash"],
        "paid_provider_calls": 0,
        "production_changed": False,
        "approval_mode": "synthetic_demonstration_only",
    }
    if not execute:
        return state
    path = base + "/criteria/" + draft["id"]
    post(
        path + "/approve-generation",
        {"plan_hash": draft["plan_hash"], "approval_ref": "synthetic-generation-budget"},
    )
    queued = post(path + "/runs", {"plan_hash": draft["plan_hash"]})
    executor, calls = synthetic_executor()
    try:
        if (
            run_criteria_one(
                app.state.sessions,
                app.state.artifact_store,
                executor=executor,
                execution_enabled=True,
                only_run_id=UUID(queued["generation_run_id"]),
            )["status"]
            != "COMPLETED"
        ):
            raise ValueError("SYNTHETIC_RUBRIC_GENERATION_FAILED")
        content = post(path + "/content", {"purpose": "synthetic-private-rubric-review"})
        review = post(
            path + "/review",
            {
                "version_sha256": content["version_sha256"],
                "decision": "approve",
                "approval_ref": "synthetic-review-not-independent-calibration",
            },
        )
        binding = {key: review[key] for key in ("version_sha256", "review_approval_id")}
        binding["criteria_id"] = draft["id"]
        baseline = {
            "key": "baseline",
            "provider": "openai",
            "model": "gpt-4.1",
            "max_input_tokens": 24000,
            "max_output_tokens": 1024,
        }
        budget = {"max_usd": "3", "max_requests": 100}
        validation = post(
            path + "/evaluation-draft",
            {
                "dataset_id": dataset["id"],
                "criteria_binding": binding,
                "baseline": baseline,
                "candidates": [
                    {**baseline, "key": "candidate", "model": "gpt-4.1-mini"},
                    {**baseline, "key": "unsafe", "model": "gpt-4.1-mini", "max_output_tokens": 64},
                ],
                "budget": budget,
                "episode_execution": {"max_turns": 1, "transport": "scripted_test_only"},
            },
        )

        def execute_plan(plan):
            post(
                base + "/experiments/plans/" + plan["id"] + "/approve",
                {
                    "plan_hash": plan["plan_hash"],
                    "approval_ref": "synthetic-target-and-judge-budget",
                },
            )
            run = post(base + "/experiments", {"plan_id": plan["id"]})
            result = run_one(
                app.state.sessions,
                app.state.artifact_store,
                executor=executor,
                execution_enabled=True,
                only_run_id=UUID(run["id"]),
            )
            if result["status"] != "COMPLETED":
                raise ValueError("SYNTHETIC_RUBRIC_EPISODE_FAILED")
            return run["id"]

        validation_run = execute_plan(validation)
        confirmation = post(
            path + "/confirmation",
            {"validation_run_id": validation_run, "candidate_key": "candidate", "budget": budget},
        )
        confirmation_run = execute_plan(confirmation)
        state.update(
            validation_run_id=validation_run,
            confirmation_run_id=confirmation_run,
            native_fixture_calls=len(calls),
            semantic_quality="NOT VERIFIED",
            source_patch_eligible=False,
        )
        return state
    finally:
        executor.close()
