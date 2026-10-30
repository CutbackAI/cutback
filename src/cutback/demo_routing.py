"""Owned native-transport routing walkthrough. No vendor network or ambient-key fallback."""

import json
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4


@contextmanager
def routing_worker(app, project_id):
    """Execute only this sample project's approved fixture routing plans."""
    from sqlalchemy import select

    from cutback.server.db import ExperimentPlanRecord, ExperimentRunRecord
    from cutback.server.worker import run_one

    project = UUID(str(project_id))
    stop = threading.Event()
    errors = []
    executor, calls = synthetic_executor()

    def work():
        try:
            while not stop.wait(0.1):
                with app.state.sessions() as session:
                    identity = session.scalar(
                        select(ExperimentRunRecord.id)
                        .join(ExperimentPlanRecord)
                        .where(
                            ExperimentRunRecord.project_id == project,
                            ExperimentRunRecord.status == "QUEUED",
                            ExperimentPlanRecord.project_id == project,
                            ExperimentPlanRecord.specification["public_specification"][
                                "routing_execution"
                            ]["transport"].as_string()
                            == "scripted_test_only",
                            ExperimentPlanRecord.specification["public_specification"][
                                "evaluator_version"
                            ].as_string()
                            == "deterministic-v15",
                        )
                        .order_by(ExperimentRunRecord.created_at)
                        .limit(1)
                    )
                if identity:
                    run_one(
                        app.state.sessions,
                        app.state.artifact_store,
                        executor=executor,
                        execution_enabled=True,
                        only_run_id=identity,
                    )
        except Exception as exc:
            # Keep diagnostics content-free. A failed operation is not restarted.
            errors.append(type(exc).__name__)
            app.state.demo_routing_worker_error = "SYNTHETIC_ROUTING_WORKER_STOPPED"

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    try:
        yield {"calls": calls, "errors": errors}
    finally:
        stop.set()
        thread.join(timeout=30)
        if thread.is_alive():
            raise RuntimeError("SYNTHETIC_ROUTING_WORKER_SHUTDOWN_INCOMPLETE")
        executor.close()


def sample_cases():
    result = []
    for index in range(80):
        symbol = chr(65 + index // 26) + chr(65 + index % 26)
        priority = "urgent" if index % 4 < 2 else "normal"
        context = " Context: " + "additional owned ticket details " * 8 if index % 2 else ""
        result.append(
            {
                "id": "routing-" + symbol,
                "group": "routing-family-" + symbol,
                "input": f"Priority: {priority}. Ticket: {symbol}." + context,
                "gold": priority.capitalize(),
                "stratum": ("long" if context else "short") + "-" + priority,
            }
        )
    return result


def synthetic_executor():
    import httpx

    from cutback.evals.executors import OpenAIExecutor
    from cutback.evals.experiments import route_target

    calls = []
    models = {"gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano"}

    def handle(request):
        if str(request.url) != "https://api.openai.com/v1/responses":
            raise ValueError("SYNTHETIC_ROUTING_DESTINATION_REFUSED")
        body = json.loads(request.content)
        if body.get("store") is not False or body.get("model") not in models:
            raise ValueError("SYNTHETIC_ROUTING_REQUEST_SCOPE_REFUSED")
        if not isinstance(body.get("input"), str):
            raise ValueError("SYNTHETIC_ROUTING_REQUIRES_PLAIN_TEXT_INPUT")
        text = body["input"]
        output = "Urgent" if "Priority: urgent." in text else "Normal"
        if body["model"] == "gpt-4.1-mini" and len(text.encode()) > 100:
            output = "Normal"  # An intentionally bad fixture on some long tasks.
        if body["model"] == "gpt-4.1-nano":
            output = "Normal"  # The cheapest constant fixture is not a useful router.
        calls.append({"model": body["model"], "input_bytes": len(text.encode())})
        return httpx.Response(
            200,
            json={
                "id": "resp_owned_" + uuid4().hex,
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": body["model"],
                "output": [
                    {
                        "id": "msg_owned",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": output, "annotations": []}],
                    }
                ],
                "usage": {
                    "input_tokens": 24,
                    "output_tokens": 2,
                    "total_tokens": 26,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    with patch.dict("os.environ", {"CUTBACK_EXPERIMENT_OPENAI_KEY": "owned-synthetic-only"}):
        native = OpenAIExecutor(
            http_client=httpx.Client(
                transport=httpx.MockTransport(handle), trust_env=False, follow_redirects=False
            )
        )

    class OwnedRoutingExecutor:
        synthetic_transport = True

        def execute(self, model, text, timeout_seconds):
            if model.provider == "router":
                model = route_target(model, text)
            # Refuse before any lazy provider construction; ambient vendor keys
            # cannot turn an unsupported fixture into a live provider call.
            if (
                model.provider != "openai"
                or model.model not in models
                or model.request_contract
                or model.native_contract
                or model.prompt_cache
            ):
                raise ValueError("SYNTHETIC_ROUTING_TARGET_REFUSED")
            return native.execute(model, text, timeout_seconds)

        def close(self):
            native.close()

    return OwnedRoutingExecutor(), calls


def seed_routing(app, control, project_id, *, execute=True, demo=False):
    """Seed only synthetic data; each plan is cryptographically fixture-only."""
    from cutback.server.worker import run_one

    base = f"/v1/projects/{project_id}"
    if demo:
        app.state.demo_routing_project = str(project_id)

    def post(path, body):
        response = control.post(path, json=body)
        if response.is_error:
            raise ValueError("SYNTHETIC_ROUTING_CONTROL_FAILED_" + str(response.status_code))
        return response.json()

    workflow = "synthetic-learned-routing"
    policy = post(
        base + "/capture-policies",
        {
            "enabled": True,
            "approval_ref": "owned-synthetic-routing-demo",
            "rights_basis": "synthetic",
            "environments": ["staging"],
            "workflows": [workflow],
            "allowed_providers": ["openai"],
            "sample_rate": 1,
            "max_samples": 4000,
            "max_payload_bytes": 512000,
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    dataset = post(
        base + "/datasets",
        {
            "workflow": workflow,
            "policy_id": policy["id"],
            "source_type": "synthetic_fixture",
            "provenance_ref": "owned-routing-example-v1",
            "independent_labels": True,
            "split_policy": "task-aware-v2",
            "cases": sample_cases(),
        },
    )
    model = {
        "key": "routing-baseline",
        "provider": "openai",
        "model": "gpt-4.1",
        "instruction": "Classify the declared ticket priority. Return only Urgent or Normal.",
        "max_input_tokens": 8192,
        "max_output_tokens": 64,
    }
    training_plan = post(
        base + "/routing/training-drafts",
        {
            "request_id": str(uuid4()),
            "content_nonce": str(uuid4()),
            "dataset_id": dataset["id"],
            "baseline": model,
            "candidates": [{**model, "key": "routing-small", "model": "gpt-4.1-mini"}],
            "metric": {
                "kind": "exact_label",
                "minimum": 1,
                "max_regression": 0,
                "min_test_samples": 3,
                "min_test_groups": 3,
            },
            "budget": {"max_usd": "2", "max_requests": 500},
            "transport": "scripted_test_only",
            "max_cases": 256,
        },
    )
    state = {
        "workflow": workflow,
        "dataset_id": dataset["id"],
        "policy_id": policy["id"],
        "training_plan_id": training_plan["id"],
        "training_plan_hash": training_plan["plan_hash"],
        "paid_provider_calls": 0,
        "production_changed": False,
        "disclosure": (
            "Owned synthetic native replies and usage; no model or customer-savings benchmark."
        ),
    }
    if not execute:
        return state
    executor, calls = synthetic_executor()

    def complete(plan):
        post(
            base + f"/experiments/plans/{plan['id']}/approve",
            {"plan_hash": plan["plan_hash"], "approval_ref": "owned-synthetic-routing-demo"},
        )
        queued = post(base + "/experiments", {"plan_id": plan["id"]})
        result = run_one(
            app.state.sessions,
            app.state.artifact_store,
            executor=executor,
            execution_enabled=True,
            only_run_id=UUID(queued["id"]),
        )
        if result["status"] != "COMPLETED":
            raise ValueError("SYNTHETIC_ROUTING_STAGE_FAILED")
        return result

    try:
        trained = complete(training_plan)
        fitted = post(
            base + "/routing/fits",
            {
                "request_id": str(uuid4()),
                "training_run_id": trained["run_id"],
                "purpose": "owned-synthetic-routing-fit",
                "options": {"max_routes": 4, "min_leaf_cases": 3, "min_training_groups": 3},
            },
        )
        path = base + "/routing/fits/" + fitted["id"]
        validation_plan = post(
            path + "/validation-drafts",
            {
                "request_id": str(uuid4()),
                "content_nonce": str(uuid4()),
                "purpose": "owned-synthetic-routing-validation",
                "budget": {"max_usd": "2", "max_requests": 500},
            },
        )
        validation = complete(validation_plan)
        confirmation_plan = post(
            path + "/confirmation-drafts",
            {
                "request_id": str(uuid4()),
                "validation_run_id": validation["run_id"],
                "purpose": "owned-synthetic-routing-confirmation",
                "budget": {"max_usd": "2", "max_requests": 500},
            },
        )
        confirmed = complete(confirmation_plan)
        if not confirmed["results"].get("lineage", {}).get("fresh_test_claim") or not confirmed[
            "results"
        ]["recommendations"][0].get("quality_gate_passed"):
            raise ValueError("SYNTHETIC_ROUTING_CONFIRMATION_NOT_COMPLETE")
        state.update(
            training_run_id=trained["run_id"],
            fit_id=fitted["id"],
            fit_sha256=fitted["version_sha256"],
            validation_plan_id=validation_plan["id"],
            validation_run_id=validation["run_id"],
            confirmation_plan_id=confirmation_plan["id"],
            confirmation_run_id=confirmed["run_id"],
            native_sdk_calls=len(calls),
            actual_declared_checks_passed=True,
            source_patch_eligible=False,
            full_task_cost_known=False,
        )
        return state
    finally:
        executor.close()
