"""Independent three-label native batch workflow; all inference stays in owned HTTP mocks."""

from datetime import UTC, datetime, timedelta
from uuid import UUID


def seed_provider_batch(app, control, project, *, headers=None, execute=True):
    from cutback.evals.datasets import DatasetImport
    from cutback.evals.experiments import ExperimentPlan
    from cutback.sample_app.batch_provider import synthetic_batch_adapter
    from cutback.sample_app.ticket_fixture import INSTRUCTION, cases, expected
    from cutback.server.provider_batch_db import ProviderBatchRecord
    from cutback.server.provider_batch_worker import run_batch_one

    base = f"/v1/projects/{project}"

    def post(path, value):
        response = control.post(
            base + path, json=value, **({"headers": headers} if headers else {})
        )
        if response.is_error:
            raise ValueError("OWNED_BATCH_CONTROL_REFUSED_" + str(response.status_code))
        return response.json()

    policy = post(
        "/capture-policies",
        {
            "enabled": True,
            "approval_ref": "owned-synthetic-batch-demo",
            "rights_basis": "synthetic",
            "environments": ["staging"],
            "workflows": ["batch-ticket-routing"],
            "allowed_providers": ["openai"],
            "retention_days": 7,
            "max_samples": 500,
            "sample_rate": 1,
            "expires_at": (datetime.now(UTC) + timedelta(days=3)).isoformat(),
        },
    )
    imported = DatasetImport.model_validate(
        {
            "workflow": "batch-ticket-routing",
            "policy_id": policy["id"],
            "source_type": "synthetic_fixture",
            "provenance_ref": "owned-batch-ticket-v1",
            "independent_labels": False,
            "split_policy": "task-aware-v2",
            "cases": cases(),
        }
    )
    dataset = post("/datasets", imported.model_dump(mode="json"))
    plan = ExperimentPlan.model_validate(
        {
            "workflow": "batch-ticket-routing",
            "dataset_hash": dataset["immutable_hash"],
            "baseline": {
                "key": "reference",
                "provider": "openai",
                "model": "gpt-4.1",
                "instruction": INSTRUCTION,
                "max_input_tokens": 8192,
                "max_output_tokens": 256,
            },
            "candidates": [
                {
                    "key": "compact",
                    "provider": "openai",
                    "model": "gpt-4.1-mini",
                    "instruction": INSTRUCTION,
                    "max_input_tokens": 8192,
                    "max_output_tokens": 64,
                }
            ],
            "metric": {
                "kind": "exact_label",
                "minimum": 0.9,
                "min_test_samples": 30,
                "min_test_groups": 10,
            },
            "criteria": [
                {
                    "key": "valid-ticket-category",
                    "required": True,
                    "metric": {
                        "kind": "allowed_label",
                        "minimum": 1,
                        "allowed_labels": ["billing", "access", "technical"],
                    },
                }
            ],
            "budget": {"max_usd": "2", "max_requests": 200, "max_wall_seconds": 300},
            "split": "validation",
            "approved_providers": ["openai"],
        }
    )
    frozen = post(
        "/experiments/plans",
        {"dataset_id": dataset["id"], "specification": plan.model_dump(mode="json")},
    )
    batch = post("/provider-batches", {"plan_id": frozen["id"], "provider_storage_approved": True})
    result = {
        "scenario": "owned-synthetic-ticket-batch.v1",
        "batch_id": batch["id"],
        "plan_id": frozen["id"],
        "reference_labels": 3,
        "declared_intent_families": 60,
        "paid_provider_calls": 0,
    }
    if not execute:
        return {**result, "status": "PENDING"}
    post(
        "/provider-batches/" + batch["id"] + "/approve",
        {
            "approval_hash": batch["approval_hash"],
            "approval_ref": "owned-synthetic-budget-and-storage",
        },
    )
    adapter, state, requests = synthetic_batch_adapter(expected)
    try:
        for _ in range(32):
            # Advance this owned fixture without waiting the production poll
            # interval. Native job lifecycle and canonical reservations remain real.
            with app.state.sessions.begin() as session:
                row = session.get(ProviderBatchRecord, UUID(batch["id"]))
                row.available_at = datetime.now(UTC) - timedelta(seconds=1)
            step = run_batch_one(
                app.state.sessions,
                app.state.artifact_store,
                adapter=adapter,
                execution_enabled=True,
                only_batch_id=batch["id"],
            )
            if step["status"] == "COMPLETED":
                break
            if step["status"] in {"UNCERTAIN", "FAILED", "NO_ELIGIBLE_BATCH"}:
                raise ValueError("OWNED_BATCH_FIXTURE_DID_NOT_COMPLETE")
        else:
            raise ValueError("OWNED_BATCH_FIXTURE_STEP_BOUND")
        with app.state.sessions() as session:
            row = session.get(ProviderBatchRecord, UUID(batch["id"]))
            return {
                **result,
                "status": row.state,
                "report": row.report,
                "provider_batch_submissions": state["submissions"],
                "native_control_requests": len(requests),
                "vendor_files_deleted": all(
                    part.get("cleanup") == "deleted" for part in row.pieces
                ),
            }
    finally:
        adapter.close()
