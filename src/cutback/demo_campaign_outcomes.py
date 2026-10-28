"""Ordinary agent tools through synthetic later outcomes. No external model calls."""

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from cutback.core.workspace import private_directory

APPLICATION_CHECK = """import json
import httpx
from app import classify

requests = []
def respond(request):
    assert str(request.url) == "https://api.openai.com/v1/responses"
    body = json.loads(request.content)
    assert body["store"] is False and body["input"] == "Owned application check"
    assert body["model"] in {"gpt-4.1", "gpt-4.1-mini"}
    assert 16 <= body["max_output_tokens"] <= 256
    assert isinstance(body["instructions"], str)
    requests.append(body)
    return httpx.Response(200, json={
        "id":"resp_application_check", "object":"response", "created_at":1,
        "model":body["model"], "status":"completed", "output":[{
            "id":"msg_owned", "type":"message", "role":"assistant", "status":"completed",
            "content":[{"type":"output_text", "text":"A", "annotations":[]}]
        }], "usage":{"input_tokens":20, "output_tokens":1, "total_tokens":21,
        "input_tokens_details":{"cached_tokens":0}, "output_tokens_details":{"reasoning_tokens":0}}
    })
answer = classify("Owned application check", api_key="synthetic-no-vendor-key",
    http_client=httpx.Client(transport=httpx.MockTransport(respond), trust_env=False))
assert answer == "A" and len(requests) == 1
"""


def frozen_tasks(control, project, writer, workflow, release, outcomes, amount):
    """Declare fixture task results, never convert an HTTP success into business success."""
    base = f"/v1/projects/{project}"
    for outcome in outcomes:
        occurred, trace = datetime.now(UTC) - timedelta(seconds=1), uuid4().hex
        start = int(occurred.timestamp() * 1000000000)
        writer(
            "POST",
            "/v1/ingest/spans",
            {
                "spans": [
                    {
                        "trace_id": trace,
                        "span_id": "1" * 16,
                        "parent_span_id": None,
                        "operation": "synthetic.task",
                        "kind": "agent",
                        "service": "owned-classifier",
                        "workflow": workflow,
                        "release": release,
                        "started_ns": start,
                        "ended_ns": start + 100000000,
                        "status": "ok",
                    }
                ]
            },
        )
        # The caller provides a separately scoped ingestion control.
        writer(
            "POST",
            "/v1/ingest/task-outcomes",
            {
                "items": [
                    {
                        "task_id": str(uuid4()),
                        "result_id": str(uuid4()),
                        "environment": "staging",
                        "trace_id": trace,
                        "workflow": workflow,
                        "service": "owned-classifier",
                        "release": release,
                        "occurred_at": occurred.isoformat(),
                        "outcome": outcome,
                        "source": "owned-synthetic-contract",
                        "source_version": "v1",
                        "basis": "deterministic_evaluation",
                        "expected_native_attempts": 0,
                        "other_costs_declared_complete": True,
                        "components": [
                            {"key": "fixture-compute", "kind": "compute", "amount_usd": amount}
                        ],
                    }
                ]
            },
        )
    env = next(
        e["id"] for e in control("GET", base + "/environments")["items"] if e["name"] == "staging"
    )
    return control(
        "POST",
        base + f"/task-cost-snapshots?environment_id={env}",
        {
            "snapshot_id": str(uuid4()),
            "start": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "end": datetime.now(UTC).isoformat(),
            "workflow": workflow,
            "release": release,
            "expected_tasks": len(outcomes),
        },
    )


def run_sample(app, client, endpoint, admin, project, ingest_token, workspace, *, watch=False):
    from sqlalchemy import select

    from cutback.agent import analyze, prepare
    from cutback.agent_campaign import advance, proposal, review, status
    from cutback.agent_campaign import prepare as prepare_campaign
    from cutback.agent_control import Control
    from cutback.application_checks import ApplicationCommand, git, run_checks
    from cutback.application_checks import prepare as prepare_checks
    from cutback.campaign_outcomes import record_outcomes
    from cutback.demo_campaigns import synthetic_executor
    from cutback.server.campaign_db import CampaignRecord, CampaignRun
    from cutback.server.campaign_worker import advance_one
    from cutback.server.proposal_worker import run_proposal_one
    from cutback.server.worker import run_one

    control = Control(endpoint, admin, client=client)
    writer = Control(endpoint, ingest_token, client=client)
    owned = private_directory(workspace, "outcome-" + uuid4().hex)
    application = private_directory(owned, "application")
    shutil.copyfile(Path(__file__).parent / "sample_app/campaign.py", application / "app.py")
    (application / ".gitignore").write_text(".cutback/\n__pycache__/\n", encoding="utf-8")
    (application / "check_application.py").write_text(APPLICATION_CHECK, encoding="utf-8")
    (application / "cases.jsonl").write_text(
        "\n".join(
            json.dumps(
                {
                    "id": f"case-{i}",
                    "input": f"Owned synthetic {chr(65 + i // 26)}{chr(65 + i % 26)} fruit",
                    "gold": "A",
                }
            )
            for i in range(200)
        ),
        encoding="utf-8",
    )
    git(application, "init")
    git(application, "add", "app.py", "check_application.py", "cases.jsonl", ".gitignore")
    git(
        application,
        "-c",
        "user.name=Synthetic",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "Owned classifier",
    )
    initial = (application / "app.py").read_bytes()
    discovered = analyze(application)
    workflow = discovered["profiles"][0]["binding"]["workflow_id"]
    prepare(
        application,
        control,
        project,
        files=["cases.jsonl"],
        rights_basis="synthetic",
        rights_ref="owned-outcome-sample",
        max_usd="10",
        max_requests=1000,
        workflow=workflow,
        minimum=0.7,
        independent_labels=True,
        for_campaign=True,
    )
    baseline = frozen_tasks(
        control,
        project,
        writer,
        workflow,
        "synthetic-reference",
        ["success", "success"] if watch else ["success"],
        "1",
    )
    campaign = prepare_campaign(
        application,
        control,
        baseline_snapshot_id=baseline["id"],
        max_usd="10",
        max_requests=1000,
        savings_per_success="0.01",
        forecast_successful_tasks=10000,
    )
    advance(
        application,
        control,
        campaign_hash=campaign["campaign_hash"],
        approval_ref="owned-synthetic-no-vendor-spend",
    )
    sessions, store = app.state.sessions, app.state.artifact_store
    executor, requests = synthetic_executor(seeds_from_scope=True)
    try:
        for _ in range(8):
            with sessions.begin() as session:
                session.get(CampaignRecord, UUID(campaign["id"])).available_at = None
            transition = advance_one(
                sessions,
                store,
                execution_enabled=True,
                only_campaign_id=campaign["id"],
                synthetic_transport=True,
            )
            if transition["status"] == "COMPLETED":
                break
            if not transition["status"].startswith("WAITING_"):
                raise ValueError("OWNED_CAMPAIGN_STEP_STOPPED")
            phase = transition["status"].removeprefix("WAITING_").lower()
            with sessions() as session:
                run_id = session.scalar(
                    select(CampaignRun.run_id)
                    .where(
                        CampaignRun.campaign_id == UUID(campaign["id"]), CampaignRun.phase == phase
                    )
                    .order_by(CampaignRun.round_index.desc())
                    .limit(1)
                )
            worker = run_proposal_one if phase == "proposal" else run_one
            if (
                worker(
                    sessions, store, executor=executor, execution_enabled=True, only_run_id=run_id
                )["status"]
                != "COMPLETED"
            ):
                raise ValueError("OWNED_CAMPAIGN_CHILD_STOPPED")
        else:
            raise ValueError("OWNED_CAMPAIGN_STEP_BOUND")
        artifact = proposal(
            application, control, purpose="owned-synthetic-review", sample_only=True
        )
        roots = [private_directory(owned, name) for name in ("base", "proposed")]
        for tree in roots:
            shutil.copytree(
                application,
                tree,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(".cutback", "__pycache__"),
            )
        patch = owned / "evaluated-change.diff"
        patch.write_text(artifact["diff"], encoding="utf-8", newline="\n")
        git(roots[1], "apply", str(patch))
        git(roots[1], "add", "app.py")
        git(
            roots[1],
            "-c",
            "user.name=Synthetic",
            "-c",
            "user.email=synthetic@example.invalid",
            "commit",
            "-m",
            "Evaluated synthetic change",
        )
        contract = prepare_checks(
            roots[0],
            roots[1],
            [
                ApplicationCommand(
                    key="native-application-check", arguments=["check_application.py"]
                )
            ],
        )
        receipt = run_checks(contract, *roots, execute_local=True)
        checked = review(
            application,
            control,
            purpose="owned-application-check-review",
            contract=contract,
            receipt=receipt,
            base=roots[0],
            proposed=roots[1],
            coverage={w: ["native-application-check"] for w in artifact["affected_workflows"]},
            branch="cutback/owned-outcome-review",
            sample_only=True,
        )
        base = f"/v1/projects/{project}"
        control(
            "POST",
            base + f"/recommendations/{artifact['recommendation_id']}/review",
            {"decision": "accepted_for_staging", "reason": "owned-synthetic-contract-only"},
        )
        change = control(
            "POST",
            base + f"/recommendations/{artifact['recommendation_id']}/patches",
            {"approval_ref": "owned-synthetic-diff", "artifact": artifact},
        )
        later = frozen_tasks(
            control,
            project,
            writer,
            workflow,
            "synthetic-later-release",
            ["success", "failure"],
            "0.25",
        )
        observed = record_outcomes(
            application,
            control,
            candidate_snapshot_id=later["id"],
            change_id=change["id"],
            release_linkage_attested=True,
            assumptions={
                "all_optimization_runs_declared_complete": True,
                "other_optimization_costs_declared_complete": True,
                "min_tasks_per_window": 1,
                "forecast_successful_tasks": 10000,
            },
        )
        watched = None
        if watch:
            import time

            from cutback.campaign_watches import approve as approve_watch
            from cutback.campaign_watches import prepare as prepare_watch
            from cutback.campaign_watches import status as watch_status
            from cutback.server.outcome_watches import run_outcome_watch_one

            first_end = max(
                datetime.fromisoformat(later["report"]["end"]),
                datetime.fromisoformat(baseline["report"]["end"]) + timedelta(seconds=60),
            )
            policy = prepare_watch(
                application,
                control,
                change_id=change["id"],
                candidate_release="synthetic-later-release",
                first_window_end=first_end,
                expires_at=first_end + timedelta(hours=1),
                window_seconds=60,
                interval_seconds=3600,
                grace_seconds=0,
                max_windows=1,
                expected_tasks=2,
                assumptions={
                    "all_optimization_runs_declared_complete": True,
                    "other_optimization_costs_declared_complete": True,
                    "min_tasks_per_window": 2,
                    "forecast_successful_tasks": 10000,
                },
                release_linkage_attested=True,
                key="owned-automatic-followup",
            )
            approve_watch(
                application,
                control,
                approval_hash=policy["receipt"]["approval_hash"],
                approval_ref="owned-single-window-metadata-only",
            )
            deadline = time.monotonic() + 65
            while datetime.now(UTC) < first_end:
                if time.monotonic() >= deadline:
                    raise ValueError("OWNED_SYNTHETIC_OBSERVATION_WINDOW_WAIT_EXCEEDED")
                time.sleep(min(0.25, max(0, (first_end - datetime.now(UTC)).total_seconds())))
            processed = run_outcome_watch_one(
                app.state.sessions, enabled=True, only_watch_id=policy["receipt"]["id"]
            )
            watched = watch_status(application, control)
            if (
                processed.get("next_action") != "REVIEW_ROLLBACK"
                or watched["receipt"]["status"] != "EXHAUSTED"
            ):
                raise ValueError("OWNED_AUTOMATIC_OUTCOME_SAMPLE_INTEGRITY_FAILED")
        if (application / "app.py").read_bytes() != initial or observed[
            "next_action"
        ] != "REVIEW_ROLLBACK":
            raise ValueError("OWNED_OUTCOME_SAMPLE_INTEGRITY_FAILED")
        return {
            "campaign_id": campaign["id"],
            "report": status(application, control),
            "native_calls": len(requests),
            "application_check_native_calls": 2,
            "paid_provider_calls": 0,
            "review": checked,
            "outcome_review": observed,
            **({"automatic_observation": watched} if watched else {}),
            "disclosure": (
                "Owned synthetic answers, usage and later task outcomes; "
                "not customer quality or savings."
            ),
        }
    finally:
        executor.close()
