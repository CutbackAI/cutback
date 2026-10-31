"""Independent observed-workload campaign, with closed native SDK transports only."""

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4


def seed_workload_campaign(app, control, project_id, writer, workspace):
    import httpx

    from cutback.agent import load_session, selected_profile
    from cutback.agent_campaign import advance, prepare, proposal, status
    from cutback.agent_control import Control
    from cutback.application_check_discovery import discover
    from cutback.application_checks import ApplicationCheckContract, git, run_checks, verify
    from cutback.campaign_operator import CampaignOperator
    from cutback.demo_workload_portfolio import seed_workload_portfolio
    from cutback.evals.executors import OpenAIExecutor
    from cutback.sample_app.workload_reuse import fixture_response

    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    basis = seed_workload_portfolio(app, control, project_id, workspace, writer, execute=False)
    root = Path(basis["source_directory"])
    original = (root / "app.py").read_bytes()
    endpoint = str(control.base_url).rstrip("/")
    adapter = Control(endpoint, control.headers["authorization"][7:], client=control)
    base = f"/v1/projects/{project_id}"
    state = load_session(root)
    workflow = state["selected_workflow"]

    def post(path, body, *, ingest=False):
        response = control.post(
            path,
            json=body,
            **({"headers": {"Authorization": "Bearer " + writer}} if ingest else {}),
        )
        response.raise_for_status()
        return response.json()

    # Owner-declared synthetic complete-task cost, separate from the eight
    # native source observations. This is not inferred from their token counts.
    occurred = datetime.now(UTC) - timedelta(seconds=1)
    start = int(occurred.timestamp() * 1_000_000_000)
    trace = uuid4().hex
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
                    "service": "synthetic-workload-campaign",
                    "workflow": workflow,
                    "release": "synthetic-reference",
                    "started_ns": start,
                    "ended_ns": start + 100_000_000,
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
                    "service": "synthetic-workload-campaign",
                    "release": "synthetic-reference",
                    "occurred_at": occurred.isoformat(),
                    "outcome": "success",
                    "source": "owned-synthetic-declaration",
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
        row["id"]
        for row in control.get(base + "/environments").json()["items"]
        if row["name"] == "staging"
    )
    snapshot = post(
        base + f"/task-cost-snapshots?environment_id={environment}",
        {
            "snapshot_id": str(uuid4()),
            "workflow": workflow,
            "expected_tasks": 1,
            "start": (occurred - timedelta(seconds=1)).isoformat(),
            "end": datetime.now(UTC).isoformat(),
        },
    )
    prepared = prepare(
        root,
        adapter,
        baseline_snapshot_id=snapshot["id"],
        max_usd="10",
        max_requests=1500,
        savings_per_success="0.1",
        forecast_successful_tasks=1000,
        rounds=1,
        variants_per_round=1,
    )
    advance(
        root,
        adapter,
        campaign_hash=prepared["campaign_hash"],
        approval_ref="owned-synthetic-workload-campaign-only",
    )
    calls = []

    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        if "text" in body:
            raise ValueError("SUCCESSFUL_CACHE_SAMPLE_MUST_SKIP_PROPOSER")
        return fixture_response(request)

    def preflight():
        current = load_session(root)
        selected_profile(root, current, workflow)
        if status(root, adapter)["campaign_hash"] != prepared["campaign_hash"]:
            raise ValueError("SYNTHETIC_CAMPAIGN_APPROVAL_CHANGED")

    with patch.dict(
        "os.environ",
        {
            "CUTBACK_CACHE_ENABLED": "true",
            "CUTBACK_EXPERIMENT_OPENAI_KEY": "owned-synthetic-only",
        },
    ):
        executor = OpenAIExecutor(
            http_client=httpx.Client(
                transport=httpx.MockTransport(respond),
                trust_env=False,
                follow_redirects=False,
            )
        )
        executor.synthetic_transport = True
        try:
            completed = CampaignOperator(
                app.state.sessions,
                app.state.artifact_store,
                project_id=project_id,
                campaign_id=prepared["id"],
                campaign_hash=prepared["campaign_hash"],
                preflight=preflight,
                executor=executor,
            ).run(poll_seconds=0.1)
            if completed["status"] != "COMPLETED":
                raise ValueError("SYNTHETIC_WORKLOAD_CAMPAIGN_DID_NOT_COMPLETE")
        finally:
            executor.close()
    report = status(root, adapter)
    if report["summary"].get("search_decision") != (
        "static_alternative_passed_skip_instruction_generation"
    ):
        raise ValueError("SYNTHETIC_WORKLOAD_MUST_SKIP_INSTRUCTION_GENERATION")
    try:
        proposal(root, adapter, purpose="owned-real-gate-must-refuse")
    except ValueError as exc:
        if str(exc) != "CAMPAIGN_INDEPENDENT_CONFIRMATION_GATE_REQUIRED":
            raise
    else:
        raise ValueError("SYNTHETIC_WORKLOAD_CANNOT_AUTHORIZE_REAL_RELEASE")
    artifact = proposal(root, adapter, purpose="owned-fixture-change-review", sample_only=True)
    proposed = root.parent / (root.name + "-campaign-proposed")
    shutil.copytree(root, proposed, ignore=shutil.ignore_patterns(".cutback", "__pycache__"))
    change = root.parent / (root.name + "-campaign.patch")
    change.write_text(artifact["diff"], encoding="utf-8", newline="")
    git(proposed, "apply", str(change))
    git(proposed, "add", ".")
    git(
        proposed,
        "-c",
        "user.name=Synthetic Cutback Sample",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "Review workload campaign",
    )
    checks = ApplicationCheckContract.model_validate(discover(root, proposed)["contract"])
    receipt = run_checks(checks, root, proposed, execute_local=True)
    verified = verify(checks, receipt, root, proposed)
    if not receipt["all_passed"]:
        raise ValueError("SYNTHETIC_WORKLOAD_CAMPAIGN_APPLICATION_CHECK_FAILED")
    change.write_text(artifact["rollback_diff"], encoding="utf-8", newline="")
    git(proposed, "apply", str(change))
    if (root / "app.py").read_bytes() != original or (proposed / "app.py").read_bytes() != original:
        raise ValueError("SYNTHETIC_WORKLOAD_CAMPAIGN_ROLLBACK_MISMATCH")
    return {
        **basis,
        "phase": "COMPLETED",
        "campaign_id": prepared["id"],
        "report": report,
        "baseline_snapshot_id": snapshot["id"],
        "template_plan_id": basis["plan_id"],
        "native_calls": len(calls),
        "source_diff": artifact,
        "application_checks": {"receipt": receipt, "verified": verified},
        "rollback_verified": True,
        "paid_provider_calls": 0,
        "runtime_cache_activated": False,
        "real_model_quality_verified": False,
        "real_customer_savings_verified": False,
    }
