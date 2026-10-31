"""Independent native-HTTP scenario fixtures; no provider request leaves the process."""

import json
import threading
from contextlib import contextmanager
from unittest.mock import patch


@contextmanager
def scenario_worker(app, project_id):
    """Serve only this synthetic sample's fixture proposals and linked development runs."""
    from uuid import UUID

    from sqlalchemy import and_, or_, select

    from cutback.server.db import ExperimentPlanRecord, ExperimentRunRecord, JobRecord
    from cutback.server.scenario_db import ScenarioDatasetImport
    from cutback.server.scenario_worker import run_scenario_one
    from cutback.server.worker import run_one

    project = UUID(str(project_id))
    stop = threading.Event()
    executor, calls = synthetic_executor()
    errors = []

    def work():
        try:
            while not stop.wait(0.1):
                with app.state.sessions() as session:
                    candidate = session.execute(
                        select(ExperimentRunRecord.id, JobRecord.execution_kind)
                        .join(JobRecord, JobRecord.run_id == ExperimentRunRecord.id)
                        .join(
                            ExperimentPlanRecord,
                            ExperimentPlanRecord.id == ExperimentRunRecord.plan_id,
                        )
                        .outerjoin(
                            ScenarioDatasetImport,
                            and_(
                                ScenarioDatasetImport.dataset_id == ExperimentPlanRecord.dataset_id,
                                ScenarioDatasetImport.project_id == project,
                            ),
                        )
                        .where(
                            ExperimentRunRecord.project_id == project,
                            ExperimentRunRecord.status == "QUEUED",
                            ExperimentPlanRecord.project_id == project,
                            or_(
                                and_(
                                    JobRecord.execution_kind == "scenario_generation",
                                    ExperimentPlanRecord.specification["public_specification"][
                                        "transport"
                                    ].as_string()
                                    == "scripted_test_only",
                                ),
                                and_(
                                    JobRecord.execution_kind == "evaluation",
                                    ScenarioDatasetImport.dataset_id.is_not(None),
                                ),
                            ),
                        )
                        .order_by(ExperimentRunRecord.created_at)
                        .limit(1)
                    ).first()
                if candidate:
                    action = run_scenario_one if candidate[1] == "scenario_generation" else run_one
                    action(
                        app.state.sessions,
                        app.state.artifact_store,
                        executor=executor,
                        execution_enabled=True,
                        only_run_id=candidate[0],
                    )
        except Exception as exc:
            errors.append(type(exc).__name__)
            app.state.demo_scenario_worker_error = "SYNTHETIC_SCENARIO_WORKER_STOPPED"

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    try:
        yield calls, errors
    finally:
        stop.set()
        thread.join(timeout=30)
        if thread.is_alive():
            raise RuntimeError("SYNTHETIC_SCENARIO_WORKER_SHUTDOWN_INCOMPLETE")
        executor.close()


def synthetic_executor(*, invalid=False, outage=False, unknown_usage=False, before_response=None):
    import httpx

    from cutback.demo import fixture_response
    from cutback.evals.executors import OpenAIExecutor

    calls = []

    def handle(request):
        if str(request.url) != "https://api.openai.com/v1/responses":
            raise ValueError("SYNTHETIC_SCENARIO_DESTINATION_REFUSED")
        body = json.loads(request.content)
        if not (body.get("instructions") or "").startswith("Propose task scenario"):
            if body.get("store") is not False:
                raise ValueError("SYNTHETIC_SCENARIO_TARGET_STORAGE_REFUSED")
            calls.append(body)
            return fixture_response(request)
        frame = json.loads(body["input"])
        if (
            body.get("store") is not False
            or frame.get("unseen_test_rows_supplied") is not False
            or frame.get("gold_and_tool_results_supplied") is not False
        ):
            raise ValueError("SYNTHETIC_SCENARIO_PRIVATE_TRAINING_FRAME_REQUIRED")
        calls.append(body)
        if before_response is not None:
            before_response()
        if outage:
            raise httpx.ReadTimeout("synthetic interruption", request=request)
        result = fixture_response(request).json()
        result["output"][0]["content"][0]["text"] = (
            "invalid synthetic response"
            if invalid
            else json.dumps(
                {
                    "personas": [
                        {
                            "key": "hurried-user",
                            "role": "customer",
                            "communication_style": "concise",
                            "situation": "limited time",
                        }
                    ],
                    "scenarios": [
                        {
                            "source_anchor": frame["training"][0]["anchor"],
                            "persona_key": "hurried-user",
                            "coverage_goal_keys": [
                                frame["requirements"]["coverage_goals"][0]["key"]
                            ],
                            "additional_context": "Please keep the response concise.",
                            "user_followups": [],
                        }
                    ],
                }
            )
        )
        size = len(body["input"].encode()) + len(body["instructions"].encode())
        if unknown_usage:
            result.pop("usage", None)
        else:
            result["usage"].update(input_tokens=size, output_tokens=256, total_tokens=size + 256)
        return httpx.Response(200, json=result)

    with patch.dict("os.environ", {"CUTBACK_EXPERIMENT_OPENAI_KEY": "synthetic-no-provider-key"}):
        executor = OpenAIExecutor(
            http_client=httpx.Client(
                transport=httpx.MockTransport(handle), trust_env=False, follow_redirects=False
            )
        )
    executor.synthetic_transport = True
    return executor, calls


def seed_scenarios(app, control, project_id, *, execute=True):
    from datetime import UTC, datetime, timedelta
    from uuid import UUID, uuid4

    from cutback.demo import sample_cases
    from cutback.server.scenario_worker import run_scenario_one
    from cutback.server.worker import run_one

    base = f"/v1/projects/{project_id}"

    def post(path, body):
        response = control.post(path, json=body)
        if response.is_error:
            raise ValueError("SYNTHETIC_SCENARIO_CONTROL_FAILED_" + str(response.status_code))
        return response.json()

    workflow = "synthetic-generated-scenarios"
    post(base + "/environments", {"name": "staging"})
    policy = post(
        base + "/capture-policies",
        {
            "enabled": True,
            "approval_ref": "synthetic-scenario-content",
            "rights_basis": "synthetic",
            "environments": ["staging"],
            "workflows": [workflow],
            "allowed_providers": ["openai"],
            "sample_rate": 1,
            "max_samples": 200,
            "max_payload_bytes": 256000,
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    source = post(
        base + "/datasets",
        {
            "workflow": workflow,
            "policy_id": policy["id"],
            "source_type": "synthetic_fixture",
            "provenance_ref": "independent-generated-scenarios-v1",
            "independent_labels": False,
            "representative": False,
            "cases": sample_cases()[:40],
        },
    )
    drafted = post(
        base + "/scenarios/drafts",
        {
            "request_id": str(uuid4()),
            "dataset_id": source["id"],
            "requirements": {
                "task_brief": (
                    "Classify synthetic support tickets while preserving category and priority."
                ),
                "coverage_goals": [
                    {
                        "key": "concise",
                        "description": "A customer with limited time requests a concise response.",
                    }
                ],
            },
            "generator": {
                "key": "scenario-generator",
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
        "source_dataset_id": source["id"],
        "scenario_id": drafted["id"],
        "generation_plan_hash": drafted["plan_hash"],
        "paid_provider_calls": 0,
        "production_changed": False,
        "approval_mode": "synthetic_demonstration_only",
    }
    if not execute:
        return state
    path = base + "/scenarios/" + drafted["id"]
    post(
        path + "/approve-generation",
        {"plan_hash": drafted["plan_hash"], "approval_ref": "synthetic-scenario-generation-budget"},
    )
    queued = post(path + "/runs", {"plan_hash": drafted["plan_hash"]})
    executor, calls = synthetic_executor()
    try:
        result = run_scenario_one(
            app.state.sessions,
            app.state.artifact_store,
            executor=executor,
            execution_enabled=True,
            only_run_id=queued["generation_run_id"],
        )
        if result["status"] != "COMPLETED":
            raise ValueError("SYNTHETIC_SCENARIO_GENERATION_FAILED")
        content = post(path + "/content", {"purpose": "synthetic-scenario-proposal-review"})
        reviewed = post(
            path + "/review",
            {
                "version_sha256": content["version_sha256"],
                "decision": "approve",
                "approval_ref": "synthetic-review-not-human-verification",
            },
        )
        request = {
            "request_id": str(uuid4()),
            "version_sha256": content["version_sha256"],
            "review_approval_id": reviewed["review_approval_id"],
            "approval_ref": "synthetic-development-import",
        }
        imported = post(path + "/imports", request)
        repeated = post(path + "/imports", request)
        baseline = {
            "key": "baseline",
            "provider": "openai",
            "model": "gpt-4.1",
            "instruction": "Return the declared category and priority as JSON.",
            "max_input_tokens": 8192,
            "max_output_tokens": 256,
        }
        plan = post(
            base + "/experiments/plans",
            {
                "request_id": str(uuid4()),
                "dataset_id": imported["dataset_id"],
                "specification": {
                    "workflow": workflow,
                    "dataset_hash": imported["immutable_hash"],
                    "split": "validation",
                    "baseline": baseline,
                    "candidates": [{**baseline, "key": "candidate", "model": "gpt-4.1-mini"}],
                    "metric": {
                        "kind": "field_f1",
                        "minimum": 0.9,
                        "min_test_samples": 1,
                        "min_test_groups": 1,
                    },
                    "budget": {"max_usd": "1", "max_requests": 20},
                    "approved_providers": ["openai"],
                },
            },
        )
        post(
            base + f"/experiments/plans/{plan['id']}/approve",
            {
                "plan_hash": plan["plan_hash"],
                "approval_ref": "synthetic-development-validation-budget",
            },
        )
        evaluated = post(base + "/experiments", {"plan_id": plan["id"]})
        result = run_one(
            app.state.sessions,
            app.state.artifact_store,
            executor=executor,
            execution_enabled=True,
            only_run_id=UUID(evaluated["id"]),
        )
        if result["status"] != "COMPLETED":
            raise ValueError("SYNTHETIC_SCENARIO_DEVELOPMENT_VALIDATION_FAILED")
        return {
            **state,
            "development_dataset_id": imported["dataset_id"],
            "generation_run_id": queued["generation_run_id"],
            "validation_run_id": evaluated["id"],
            "idempotent_import_checked": repeated["existing_import"],
            "native_sdk_calls": len(calls),
            "scenario_variants": len(content["proposal"]["scenarios"]),
            "coverage_scope": content["preview"]["coverage_scope"],
            "independent_labels": False,
            "representative": False,
            "test_evidence": False,
            "reactive_user_simulation": False,
        }
    finally:
        executor.close()
