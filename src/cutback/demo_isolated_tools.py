"""Real HTTP/native SDK/PostgreSQL/separate-process/isolated SQL sample, no vendor I/O."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4


def seed_isolated_tools(app, control, project, image_id, *, headers=None):
    from sqlalchemy import select

    from cutback.evals.experiments import ExperimentPlan
    from cutback.sample_app.isolated_records import record_case, synthetic_record_executor
    from cutback.sample_app.tool_service import owned_tool_service
    from cutback.server.tool_db import ToolOperationRecord, ToolSessionRecord
    from cutback.server.worker import run_one

    project, principal, broker = UUID(str(project)), uuid4(), uuid4()
    workflow, base = "isolated-record-task", f"/v1/projects/{project}"

    def post(path, body):
        response = control.post(base + path, json=body, **({"headers": headers} if headers else {}))
        if response.is_error:
            raise ValueError("OWNED_ISOLATED_TOOL_CONTROL_REFUSED_" + str(response.status_code))
        return response.json()

    policy = post(
        "/capture-policies",
        {
            "enabled": True,
            "approval_ref": "owned-isolated-record-sample",
            "rights_basis": "synthetic",
            "environments": ["staging"],
            "workflows": [workflow],
            "allowed_providers": ["openai"],
            "max_samples": 64,
            "sample_rate": 1,
            "max_payload_bytes": 64000,
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    dataset = post(
        "/datasets",
        {
            "workflow": workflow,
            "environment": "staging",
            "policy_id": policy["id"],
            "source_type": "synthetic_fixture",
            "provenance_ref": "owned-isolated-record-v1",
            "independent_labels": False,
            "representative": False,
            "cases": [record_case(project, principal) for _ in range(20)],
        },
    )
    spec = ExperimentPlan.model_validate(
        {
            "name": "Read resolve and verify isolated records",
            "workflow": workflow,
            "dataset_hash": dataset["immutable_hash"],
            "evaluator_version": "deterministic-v8",
            "content_nonce": str(uuid4()),
            "sample_limit": 2,
            "retain_case_outputs": True,
            "baseline": {"key": "reference", "provider": "openai", "model": "gpt-4.1"},
            "candidates": [{"key": "compact", "provider": "openai", "model": "gpt-4.1-mini"}],
            "metric": {"kind": "episode_contract", "minimum": 1},
            "approved_providers": ["openai"],
            "budget": {"max_usd": "5", "max_requests": 32, "max_wall_seconds": 300},
            "episode_execution": {
                "version": "native-isolated-record-episode-v1",
                "transport": "scripted_test_only",
                "max_turns": 4,
                "max_tool_calls": 3,
                "isolated_tools": {
                    "project_id": str(project),
                    "principal_id": str(principal),
                    "broker_id": str(broker),
                    "environment": "staging",
                    "image_id": image_id,
                    "content_use_approved": True,
                    "max_sessions": 8,
                    "max_total_tool_calls": 24,
                },
            },
        }
    )
    plan = post(
        "/experiments/plans",
        {"dataset_id": dataset["id"], "specification": spec.model_dump(mode="json")},
    )
    post(
        "/experiments/plans/" + plan["id"] + "/approve",
        {"plan_hash": plan["plan_hash"], "approval_ref": "owned-scripted-model-real-isolated-sql"},
    )
    queued = post("/experiments", {"plan_id": plan["id"]})
    executor = synthetic_record_executor()
    try:
        with owned_tool_service(project, principal, broker, image_id, workflow=workflow) as client:
            result = run_one(
                app.state.sessions,
                app.state.artifact_store,
                executor=executor,
                execution_enabled=True,
                only_run_id=queued["id"],
                tool_client=client,
            )
        if result["status"] != "COMPLETED":
            raise ValueError("OWNED_ISOLATED_RECORD_TASK_DID_NOT_COMPLETE")
        with app.state.sessions() as session:
            receipts = session.scalars(
                select(ToolSessionRecord).where(ToolSessionRecord.run_id == UUID(queued["id"]))
            ).all()
            operations = session.scalars(
                select(ToolOperationRecord).where(ToolOperationRecord.run_id == UUID(queued["id"]))
            ).all()
            if not receipts or not all(row.cleanup_confirmed for row in receipts):
                raise ValueError("OWNED_ISOLATED_RECORD_CONTAINMENT_UNCONFIRMED")
            return {
                "run_id": queued["id"],
                "dataset_id": dataset["id"],
                "plan_id": plan["id"],
                "principal_id": str(principal),
                "broker_id": str(broker),
                "image_id": image_id,
                "sessions": len(receipts),
                "operations": len(operations),
                "state_changes": sum(row.state_changed is True for row in operations),
                "cleanup_confirmed": True,
                "paid_provider_calls": 0,
                "tool_cost_usd": None,
                "complete_task_cost_usd": None,
                "disclosure": (
                    "Scripted native SDK replies; actual separate process, isolated SQL and "
                    "canonical receipts. No customer/model-quality claim."
                ),
            }
    finally:
        executor.close()
