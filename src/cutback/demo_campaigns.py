"""Independent, source-bound adaptive campaign with actual SDKs and owned transports.

The provider answers, token counts and business cost are synthetic fixtures.
No real model quality, customer savings or production readiness is asserted.
"""

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4


def synthetic_executor(*, seeds_from_scope=False):
    import httpx

    from cutback.evals.executors import OpenAIExecutor

    requests = []

    def respond(request):
        if str(request.url) != "https://api.openai.com/v1/responses":
            raise ValueError("SYNTHETIC_CAMPAIGN_DESTINATION_REFUSED")
        body = json.loads(request.content)
        if body.get("store") is not False:
            raise ValueError("SYNTHETIC_CAMPAIGN_STORAGE_POLICY_REFUSED")
        requests.append(body)
        proposer = "text" in body
        if proposer:
            seeds = (
                [seed["key"] for seed in json.loads(body["input"])["seeds"]]
                if seeds_from_scope
                else ("baseline", "simple-downgrade")
            )
            output = json.dumps(
                {
                    "proposals": [
                        {
                            "seed_key": key,
                            "instruction": "Answer A only.",
                            "max_output_tokens": 64 + index * 16 if seeds_from_scope else 64,
                        }
                        for index, key in enumerate(seeds)
                    ]
                }
            )
        else:
            output = (
                "B" if body["model"] == "gpt-4.1-mini" and body["max_output_tokens"] == 256 else "A"
            )
        tokens = 160 if proposer else 50 if body["max_output_tokens"] == 256 else 4
        return httpx.Response(
            200,
            json={
                "id": "resp_synthetic_" + uuid4().hex,
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": body["model"],
                **({"service_tier": "default"} if body["model"] == "gpt-6-luna" else {}),
                "output": [
                    {
                        "type": "message",
                        "id": "msg_synthetic",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": output, "annotations": []}],
                    }
                ],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": tokens,
                    "total_tokens": 100 + tokens,
                    "input_tokens_details": {
                        "cached_tokens": 0,
                        **(
                            {
                                "cache_write_tokens": 0,
                                "cache_write_tokens_details": {
                                    "ttl_30m_tokens": 0,
                                    "ttl_12h_tokens": 0,
                                },
                            }
                            if body["model"] == "gpt-6-luna"
                            else {}
                        ),
                    },
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    with patch.dict("os.environ", {"CUTBACK_EXPERIMENT_OPENAI_KEY": "synthetic-no-provider-key"}):
        executor = OpenAIExecutor(
            http_client=httpx.Client(
                transport=httpx.MockTransport(respond),
                trust_env=False,
                follow_redirects=False,
            )
        )
    executor.synthetic_transport = True
    return executor, requests


def seed_campaign(app, control, project_id, writer, workspace):
    from cutback.campaign_operator import CampaignOperator
    from cutback.core.instruction_edits import qualify_instruction_edit
    from cutback.core.request_profiles import prepare_profile
    from cutback.core.workspace import private_directory
    from cutback.evals.experiments import ExperimentPlan, ModelSpec
    from cutback.scanner import scan

    base = f"/v1/projects/{project_id}"

    def post(path, body, *, ingest=False):
        response = control.post(
            path,
            json=body,
            **({"headers": {"Authorization": "Bearer " + writer}} if ingest else {}),
        )
        response.raise_for_status()
        return response.json()

    workspace.mkdir(parents=True, exist_ok=True)
    application = private_directory(workspace, "campaign-classifier")
    if (application / "app.py").is_symlink():
        raise ValueError("SYNTHETIC_CAMPAIGN_SOURCE_PATH_UNSAFE")
    shutil.copyfile(Path(__file__).parent / "sample_app/campaign.py", application / "app.py")
    inventory = scan(application)
    site = next(s for s in inventory["call_sites"] if s["sdk_surface"] == "responses.create")
    profile = prepare_profile(application, site, commit=inventory["project"].get("commit"))
    workflow = profile.binding.workflow_id
    inventory["request_profiles"] = [profile.binding.model_dump(mode="json")]
    post(base + "/scans", inventory)
    policy = post(
        base + "/capture-policies",
        {
            "enabled": True,
            "approval_ref": "owned-synthetic-campaign-only",
            "rights_basis": "synthetic",
            "environments": ["staging"],
            "workflows": [workflow],
            "allowed_providers": ["openai"],
            "sample_rate": 1,
            "max_samples": 200,
            "retention_days": 1,
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    dataset = post(
        base + "/datasets",
        {
            "workflow": workflow,
            "policy_id": policy["id"],
            "source_type": "synthetic_fixture",
            "provenance_ref": "owned-campaign-classifier-v1",
            "independent_labels": True,
            "representative": False,
            "cases": [
                {
                    "id": f"case-{i}",
                    "input": f"Classify synthetic {chr(65 + i // 26)}{chr(65 + i % 26)} fruit",
                    "gold": "A",
                }
                for i in range(40)
            ],
        },
    )
    occurred = datetime.now(UTC) - timedelta(seconds=1)
    trace = uuid4().hex
    start = int(occurred.timestamp() * 1000000000)
    post(
        "/v1/ingest/spans",
        {
            "spans": [
                {
                    "trace_id": trace,
                    "span_id": "1" * 16,
                    "parent_span_id": None,
                    "operation": "synthetic.task",
                    "kind": "agent",
                    "service": "synthetic-campaign-classifier",
                    "workflow": workflow,
                    "release": "synthetic-reference",
                    "started_ns": start,
                    "ended_ns": start + 100000000,
                    "status": "ok",
                }
            ]
        },
        ingest=True,
    )
    post(
        "/v1/ingest/task-outcomes",
        {
            "items": [
                {
                    "task_id": str(uuid4()),
                    "result_id": str(uuid4()),
                    "environment": "staging",
                    "trace_id": trace,
                    "workflow": workflow,
                    "service": "synthetic-campaign-classifier",
                    "release": "synthetic-reference",
                    "occurred_at": occurred.isoformat(),
                    "outcome": "success",
                    "source": "synthetic-app-checks",
                    "source_version": "v1",
                    "basis": "business_event",
                    "expected_native_attempts": 0,
                    "other_costs_declared_complete": True,
                    "components": [
                        {"key": "synthetic-compute", "kind": "compute", "amount_usd": "1"}
                    ],
                }
            ]
        },
        ingest=True,
    )
    environment = next(
        e["id"]
        for e in control.get(base + "/environments").json()["items"]
        if e["name"] == "staging"
    )
    snapshot = post(
        base + f"/task-cost-snapshots?environment_id={environment}",
        {
            "snapshot_id": str(uuid4()),
            "start": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "end": datetime.now(UTC).isoformat(),
            "workflow": workflow,
            "expected_tasks": 1,
        },
    )
    baseline = ModelSpec.model_validate(profile.baseline())
    simple = {
        **baseline.model_dump(mode="json"),
        "key": "simple-downgrade",
        "model": "gpt-4.1-mini",
    }
    plan = ExperimentPlan.model_validate(
        {
            "workflow": workflow,
            "dataset_hash": dataset["immutable_hash"],
            "name": "Synthetic classifier improvement",
            "split": "validation",
            "source_binding": profile.binding.model_dump(mode="json"),
            "baseline": baseline.model_dump(mode="json"),
            "candidates": [simple],
            "approved_providers": ["openai"],
            "metric": {
                "kind": "exact_label",
                "minimum": 1,
                "min_test_samples": 3,
                "min_test_groups": 3,
            },
            "budget": {"max_usd": "10", "max_requests": 500},
        }
    )
    template = post(
        base + "/experiments/plans",
        {"dataset_id": dataset["id"], "specification": plan.model_dump(mode="json")},
    )
    campaign = post(
        base + "/campaigns",
        {
            "request_id": str(uuid4()),
            "specification": {
                "version": "cutback.improvement-campaign.v2",
                "dataset_id": dataset["id"],
                "baseline_snapshot_id": snapshot["id"],
                "environment": "staging",
                "template": plan.model_dump(mode="json"),
                "instruction_edit": qualify_instruction_edit(application, site, profile).model_dump(
                    mode="json"
                ),
                "generator": {
                    "transport": "scripted_test_only",
                    "rounds": 1,
                    "variants_per_round": 2,
                    "model": {
                        "key": "proposer",
                        "provider": "openai",
                        "model": "gpt-4.1-mini",
                        "max_input_tokens": 24000,
                        "max_output_tokens": 2048,
                    },
                },
                "budget": {"max_usd": "10", "max_requests": 500, "max_wall_seconds": 300},
                "conservative_savings_per_success_usd": "0.01",
                "forecast_successful_tasks": 10000,
            },
        },
    )
    post(
        base + f"/campaigns/{campaign['id']}/approve",
        {
            "campaign_hash": campaign["campaign_hash"],
            "approval_ref": "owned-scripted-campaign-only-no-vendor-spend",
        },
    )
    executor, requests = synthetic_executor()
    sessions, store = app.state.sessions, app.state.artifact_store
    try:

        def source_scope():
            current = scan(application)
            selected = next(
                (s for s in current["call_sites"] if s["call_site_id"] == site["call_site_id"]),
                None,
            )
            if (
                selected is None
                or prepare_profile(
                    application, selected, commit=current["project"].get("commit")
                ).binding
                != profile.binding
            ):
                raise ValueError("SYNTHETIC_CAMPAIGN_SOURCE_CHANGED")

        driver = CampaignOperator(
            sessions,
            store,
            project_id=project_id,
            campaign_id=campaign["id"],
            campaign_hash=campaign["campaign_hash"],
            preflight=source_scope,
            executor=executor,
        )
        driven = driver.run()
        if driven["status"] != "COMPLETED":
            raise ValueError("SYNTHETIC_CAMPAIGN_DRIVER_STOPPED")
        report = control.get(base + f"/campaigns/{campaign['id']}").json()
        selected = post(
            base + f"/campaigns/{campaign['id']}/selected/content",
            {"purpose": "synthetic-source-diff-review"},
        )
        from cutback.changes import instruction_patch
        from cutback.core.instruction_edits import LiteralInstructionEdit

        artifact = instruction_patch(
            application,
            inventory,
            site["call_site_id"],
            {
                "id": selected["recommendation_id"],
                "run_id": selected["run_id"],
                "status": selected["recommendation_status"],
                "evidence": selected["evidence"],
            },
            expected_source_sha256=profile.binding.source_sha256,
            instruction_edit=LiteralInstructionEdit.model_validate(selected["instruction_edit"]),
            max_instruction_bytes=selected["max_instruction_bytes"],
            proposal_only=True,
        )
        return {
            "campaign_id": campaign["id"],
            "operator": driven,
            "template_plan_id": template["id"],
            "baseline_snapshot_id": snapshot["id"],
            "source_file": "cutback/sample_app/campaign.py",
            "native_calls": len(requests),
            "paid_provider_calls": 0,
            "transport": "scripted_test_only",
            "report": report,
            "source_diff": artifact,
            "disclosure": (
                "Synthetic answers, token counts and costs; "
                "not measured model performance or customer ROI."
            ),
        }
    finally:
        executor.close()
