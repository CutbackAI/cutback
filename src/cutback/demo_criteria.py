"""Independent synthetic criteria -> review -> validation -> untouched confirmation."""

import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4


def synthetic_executor():
    import httpx

    from cutback.demo import fixture_response
    from cutback.evals.executors import OpenAIExecutor

    calls = []

    def handle(request):
        if str(request.url) != "https://api.openai.com/v1/responses":
            raise ValueError("SYNTHETIC_CRITERIA_DESTINATION_REFUSED")
        body = json.loads(request.content)
        if body.get("store") is not False:
            raise ValueError("SYNTHETIC_CRITERIA_STORAGE_POLICY_REFUSED")
        response = fixture_response(request)
        result = response.json()
        if "Propose evaluation checks" in (body.get("instructions") or ""):
            frame = json.loads(body["input"])
            if frame.get("unseen_test_rows_supplied") is not False:
                raise ValueError("SYNTHETIC_CRITERIA_TRAINING_FRAME_REQUIRED")
            result["output"][0]["content"][0]["text"] = json.dumps(
                {
                    "criteria": [
                        {
                            "description": "Compare category and priority with task labels.",
                            "metric_kind": "field_f1",
                            "output_field": None,
                            "schema_definition": None,
                            "training_anchors": [frame["training"][0]["anchor"]],
                        }
                    ]
                }
            )
            size = len(body["input"].encode()) + len(body["instructions"].encode())
            result["usage"].update(input_tokens=size, output_tokens=128, total_tokens=size + 128)
        calls.append(body["model"])
        return httpx.Response(200, json=result)

    with patch.dict("os.environ", {"CUTBACK_EXPERIMENT_OPENAI_KEY": "synthetic-no-provider-key"}):
        executor = OpenAIExecutor(
            http_client=httpx.Client(
                transport=httpx.MockTransport(handle), trust_env=False, follow_redirects=False
            )
        )
    executor.synthetic_transport = True
    return executor, calls


def seed_criteria(app, control, project_id, *, execute=True, workspace=None):
    from cutback.demo import sample_cases
    from cutback.server.criteria_worker import run_criteria_one
    from cutback.server.worker import run_one

    base = f"/v1/projects/{project_id}"

    def post(path, body):
        response = control.post(path, json=body)
        if response.is_error:
            raise ValueError("SYNTHETIC_CRITERIA_CONTROL_FAILED_" + str(response.status_code))
        return response.json()

    workflow = "synthetic-reviewed-criteria"
    source = None
    profile = None
    inventory = None
    if workspace is not None:
        from cutback.core.request_profiles import prepare_profile
        from cutback.core.workspace import private_directory
        from cutback.demo import require_demo_git
        from cutback.scanner import scan

        require_demo_git()
        Path(workspace).mkdir(parents=True, exist_ok=True)
        source = private_directory(Path(workspace), "reviewed-criteria-" + uuid4().hex)
        shutil.copyfile(
            Path(__file__).parent / "sample_app/criteria_application.py", source / "app.py"
        )
        shutil.copyfile(
            Path(__file__).parent / "sample_app/criteria_check.py", source / "check_application.py"
        )
        for args in (
            ("init",),
            ("add", "."),
            (
                "-c",
                "user.name=Cutback synthetic sample",
                "-c",
                "user.email=synthetic@example.invalid",
                "commit",
                "-m",
                "Independent reviewed criteria application",
            ),
        ):
            subprocess.run(
                ["git", "-c", "core.hooksPath=", "-C", str(source), *args],
                check=True,
                capture_output=True,
                timeout=10,
            )
        inventory = scan(source)
        site = inventory["call_sites"][0]
        profile = prepare_profile(source, site, commit=inventory["project"].get("commit"))
        workflow = profile.binding.workflow_id
        inventory["request_profiles"] = [profile.binding.model_dump(mode="json")]
        post(base + "/scans", inventory)

    policy = post(
        base + "/capture-policies",
        {
            "enabled": True,
            "approval_ref": "owned-synthetic-criteria-only",
            "rights_basis": "synthetic",
            "environments": ["staging"],
            "workflows": [workflow],
            "allowed_providers": ["openai"],
            "sample_rate": 1,
            "max_samples": 200,
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    cases = [
        {
            **row,
            "id": "criteria-" + row["id"],
            "input": "Independent criteria application. " + row["input"],
        }
        for row in sample_cases()[:60]
    ]
    dataset = post(
        base + "/datasets",
        {
            "workflow": workflow,
            "policy_id": policy["id"],
            "source_type": "synthetic_fixture",
            "provenance_ref": "independent-criteria-v1",
            "independent_labels": True,
            "cases": cases,
        },
    )
    draft = post(
        base + "/criteria/drafts",
        {
            "dataset_id": dataset["id"],
            "requirements": {
                "task_brief": "Classify owned support tickets into category and priority.",
                "minimum": 0.9,
                "min_test_samples": 3,
                "min_test_groups": 3,
            },
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
    state = {
        "dataset_id": dataset["id"],
        "criteria_id": draft["id"],
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
        {"plan_hash": draft["plan_hash"], "approval_ref": "synthetic-generator-budget-only"},
    )
    queued = post(path + "/runs", {"plan_hash": draft["plan_hash"]})
    executor, calls = synthetic_executor()
    try:
        result = run_criteria_one(
            app.state.sessions,
            app.state.artifact_store,
            executor=executor,
            execution_enabled=True,
            only_run_id=UUID(queued["generation_run_id"]),
        )
        if result["status"] != "COMPLETED":
            raise ValueError("SYNTHETIC_CRITERIA_GENERATION_FAILED")
        content = post(path + "/content", {"purpose": "synthetic-criteria-demonstration"})
        post(
            path + "/review",
            {
                "version_sha256": content["version_sha256"],
                "decision": "approve",
                "approval_ref": "synthetic-fixture-review-not-human-calibration",
            },
        )
        approved = post(path + "/content", {"purpose": "synthetic-approved-criteria"})
        budget = {"max_usd": "1", "max_requests": 200}
        baseline = (
            profile.baseline()
            if profile
            else {
                "key": "baseline",
                "provider": "openai",
                "model": "gpt-4.1",
                "max_input_tokens": 8000,
                "max_output_tokens": 64,
            }
        )
        validation = post(
            path + "/evaluation-draft",
            {
                "dataset_id": dataset["id"],
                "criteria_binding": approved["criteria_binding"],
                "baseline": baseline,
                "candidates": [
                    {
                        **baseline,
                        "key": "candidate",
                        "model": "gpt-4.1-mini",
                    }
                ],
                "budget": budget,
                "source_binding": profile.binding.model_dump(mode="json") if profile else None,
            },
        )

        def execute_plan(plan):
            post(
                base + f"/experiments/plans/{plan['id']}/approve",
                {"plan_hash": plan["plan_hash"], "approval_ref": "synthetic-target-budget-only"},
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
                raise ValueError("SYNTHETIC_CRITERIA_TARGET_FAILED")
            return result

        checked = execute_plan(validation)
        confirmation = post(
            path + "/confirmation",
            {
                "validation_run_id": checked["run_id"],
                "candidate_key": "candidate",
                "budget": budget,
            },
        )
        confirmed = execute_plan(confirmation)
        if confirmed["results"].get("lineage", {}).get("fresh_test_claim") is not True:
            raise ValueError("SYNTHETIC_CRITERIA_UNTOUCHED_CONFIRMATION_REQUIRED")
        source_change = None
        if source is not None and inventory is not None and profile is not None:
            from cutback.changes import model_patch

            response = control.get(base + "/recommendations")
            if response.is_error:
                raise ValueError("SYNTHETIC_CRITERIA_RECOMMENDATION_UNAVAILABLE")
            recommendation = next(
                row for row in response.json() if row["run_id"] == confirmed["run_id"]
            )
            recommendation = post(
                path + f"/recommendations/{recommendation['id']}/source-evidence",
                {"purpose": "synthetic-source-diff-review"},
            )
            post(
                base + f"/recommendations/{recommendation['id']}/review",
                {"decision": "accepted_for_staging", "reason": "synthetic-fixture-review-only"},
            )
            recommendation["status"] = "accepted_for_staging"
            source_change = model_patch(
                source,
                inventory,
                profile.binding.call_site_id,
                recommendation,
                expected_source_sha256=profile.binding.source_sha256,
            )
            post(
                base + f"/recommendations/{recommendation['id']}/patches",
                {"approval_ref": "synthetic-exact-diff-review-only", "artifact": source_change},
            )
        return {
            **state,
            "generation_run_id": queued["generation_run_id"],
            "validation_run_id": checked["run_id"],
            "confirmation_run_id": confirmed["run_id"],
            "native_sdk_calls": len(calls),
            "criteria_binding": approved["criteria_binding"],
            "criteria_generation_cost": confirmed["results"]["criteria_generation_cost"],
            **({"source_directory": str(source), "source_change": source_change} if source else {}),
        }
    finally:
        executor.close()
