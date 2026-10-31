"""Observe source once; compare cache/native/prompt work under one durable campaign."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from support_http import owned_collector
from test_campaign_workflow import advance, approve, child
from test_task_outcomes import declaration, environment, ingest, spans

from cutback.agent import load_session
from cutback.core.instruction_edits import qualify_instruction_edit
from cutback.core.request_profiles import prepare_profile
from cutback.demo_workload_portfolio import seed_workload_portfolio
from cutback.evals.artifacts import EncryptedFileStore
from cutback.evals.campaigns import automatic_static_portfolio
from cutback.evals.executors import OpenAIExecutor
from cutback.evals.experiments import ExperimentPlan
from cutback.scanner import scan
from cutback.server.campaign_db import CampaignRun
from cutback.server.db import ExperimentPlanRecord, ExperimentRunRecord
from cutback.server.plan_artifacts import load_plan
from cutback.server.proposal_worker import run_proposal_one
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


def prepared(api, tenant, tmp_path, monkeypatch, *, repeats=True):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    monkeypatch.setenv("CUTBACK_CACHE_ENABLED", "true")
    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "owned-synthetic-only")
    store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    api[2].state.artifact_store = store
    with (
        owned_collector(api[2]) as endpoint,
        httpx.Client(base_url=endpoint, headers=api[1], timeout=30, trust_env=False) as control,
    ):
        basis = seed_workload_portfolio(
            api[2], control, tenant[0], tmp_path, tenant[1]["Authorization"][7:], execute=False
        )
    root = Path(basis["source_directory"])
    state = load_session(root)
    base = f"/v1/projects/{tenant[0]}"
    content = api[0].post(
        base + f"/experiments/plans/{basis['plan_id']}/content",
        headers=api[1],
        json={"purpose": "owned-cache-campaign-template"},
    )
    assert content.status_code == 200
    plan = ExperimentPlan.model_validate(content.json()["specification"])
    cases = [
        {
            "id": f"campaign-{chr(65 + group)}-{repeat}",
            "input": f"Owned workload billing family {chr(65 + group)}"
            + ("" if repeats else f" variation {chr(65 + repeat)}"),
            "gold": "billing",
            "group": f"campaign-family-{chr(65 + group)}",
        }
        for group in range(20)
        for repeat in range(10)
    ]
    dataset = api[0].post(
        base + "/datasets",
        headers=api[1],
        json={
            "workflow": plan.workflow,
            "policy_id": state["policy_id"],
            "source_type": "synthetic_fixture",
            "independent_labels": True,
            "representative": False,
            "provenance_ref": "owned-workload-campaign-cases",
            "cases": cases,
        },
    )
    assert dataset.status_code == 201, dataset.json()
    raw = plan.model_dump(mode="json")
    raw.update(
        dataset_hash=dataset.json()["immutable_hash"],
        repeats=1,
        candidates=[m.model_dump(mode="json") for m in plan.candidates if m.exact_cache],
    )
    plan = ExperimentPlan.model_validate(raw)
    inventory = scan(root)
    site = inventory["call_sites"][0]
    profile = prepare_profile(
        root, site, workflow_id=plan.workflow, commit=inventory["project"]["commit"]
    )
    edit = qualify_instruction_edit(root, site, profile)
    task = declaration(
        workflow=plan.workflow,
        components=[{"key": "owned-synthetic-compute", "kind": "compute", "amount_usd": "1"}],
    )
    spans(api, tenant, task)
    assert ingest(api, tenant, task).status_code == 200
    snapshot = api[0].post(
        base + "/task-cost-snapshots",
        headers=api[1],
        params={"environment_id": environment(api, tenant[0])},
        json={
            "snapshot_id": str(uuid4()),
            "workflow": plan.workflow,
            "expected_tasks": 1,
            "start": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "end": datetime.now(UTC).isoformat(),
        },
    )
    assert snapshot.status_code == 200, snapshot.json()
    request = {
        "request_id": str(uuid4()),
        "specification": {
            "version": "cutback.improvement-campaign.v4",
            "dataset_id": dataset.json()["id"],
            "baseline_snapshot_id": snapshot.json()["id"],
            "environment": "staging",
            "template": plan.model_dump(mode="json"),
            "instruction_edit": edit.model_dump(mode="json"),
            "static_portfolio": [
                m.model_dump(mode="json") for m in automatic_static_portfolio(plan)
            ],
            "generator": {
                "model": {
                    "key": "proposer",
                    "provider": "openai",
                    "model": "gpt-4.1-mini",
                    "max_input_tokens": 24000,
                    "max_output_tokens": 512,
                },
                "transport": "scripted_test_only",
                "variants_per_round": 1,
                "max_output_tokens": plan.baseline.max_output_tokens,
            },
            "budget": {"max_usd": "10", "max_requests": 1500, "max_wall_seconds": 300},
            "conservative_savings_per_success_usd": "0.1",
            "forecast_successful_tasks": 1000,
        },
    }
    response = api[0].post(base + "/campaigns", headers=api[1], json=request)
    assert response.status_code == 201, response.json()
    return root, base, store, response.json(), request


@pytest.mark.parametrize("repeats", [True, False])
def test_workload_cache_wins_or_uses_only_the_reserved_native_prompt_fallback(
    api, tenant, tmp_path, monkeypatch, repeats
):
    from cutback.sample_app.workload_reuse import fixture_response

    root, base, store, row, request = prepared(api, tenant, tmp_path, monkeypatch, repeats=repeats)
    assert row["economic_envelope"]["contract"] == "cutback.campaign-budget.v4"
    assert row["economic_envelope"]["instruction_search_enabled"]
    assert api[0].post(base + "/campaigns", headers=api[1], json=request).json()["id"] == row["id"]
    approve(api, base, row)
    calls = []

    def provider(native_request):
        body = json.loads(native_request.content)
        calls.append(body)
        response = fixture_response(native_request).json()
        if "text" in body:
            scope = json.loads(body["input"])
            cache_keys = {
                m["key"] for m in row["specification"]["static_portfolio"] if m.get("exact_cache")
            }
            assert not cache_keys & {seed["key"] for seed in scope["seeds"]}
            response["output"][0]["content"][0]["text"] = json.dumps(
                {
                    "proposals": [
                        {
                            "seed_key": "baseline",
                            "instruction": "Owned shorter billing answer.",
                            "max_output_tokens": 32,
                        }
                    ]
                }
            )
            response["usage"].update(output_tokens=80, total_tokens=180)
        elif body.get("instructions") == "Owned shorter billing answer.":
            response["usage"].update(output_tokens=1, total_tokens=101)
        return httpx.Response(200, json=response)

    executor = OpenAIExecutor(
        http_client=httpx.Client(transport=httpx.MockTransport(provider), trust_env=False)
    )
    executor.synthetic_transport = True

    def evaluate(phase):
        result = run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=child(api, row, phase),
        )
        assert result["status"] == "COMPLETED", result

    try:
        assert advance(api, store, row)["status"] == "WAITING_VALIDATION"
        evaluate("validation")
        with api[2].state.sessions() as session:
            initial_frontier = session.get(
                ExperimentRunRecord, child(api, row, "validation")
            ).results["search_frontier"]
        assert advance(api, store, row)["status"] == (
            "WAITING_CONFIRMATION" if repeats else "WAITING_PROPOSAL"
        ), [
            {
                k: item.get(k)
                for k in ("key", "engine", "eligible", "cost_per_request_usd", "reason")
            }
            for item in initial_frontier["candidates"]
        ]
        if not repeats:
            assert (
                run_proposal_one(
                    api[2].state.sessions,
                    store,
                    executor=executor,
                    execution_enabled=True,
                    only_run_id=child(api, row, "proposal"),
                )["status"]
                == "COMPLETED"
            )
            assert advance(api, store, row)["status"] == "WAITING_VALIDATION"
            evaluate("validation")
            assert advance(api, store, row)["status"] == "WAITING_CONFIRMATION"
        evaluate("confirmation")
        assert advance(api, store, row)["status"] == "COMPLETED"
    finally:
        executor.close()
    report = api[0].get(base + f"/campaigns/{row['id']}", headers=tenant[2]).json()
    assert (
        report["billing_complete"]
        and report["requests"] <= row["economic_envelope"]["max_requests"]
    )
    assert len([r for r in calls if "text" in r]) == int(not repeats)
    with api[2].state.sessions() as session:
        links = session.query(CampaignRun).filter_by(campaign_id=UUID(row["id"])).all()
        for link in links:
            executed = session.get(ExperimentRunRecord, link.run_id)
            if link.phase == "proposal":
                continue
            frozen = load_plan(
                store,
                UUID(tenant[0]),
                session.get(ExperimentPlanRecord, executed.plan_id).specification,
            )
            assert frozen.workload_portfolio is not None
            if frozen.split == "validation":
                assert any(m.exact_cache for m in frozen.candidates)
            else:
                assert executed.results["lineage"]["fresh_test_claim"] is True
                assert bool(frozen.candidates[0].exact_cache) is repeats
    assert (root / "app.py").exists()
    import shutil

    from cutback.agent_campaign import proposal
    from cutback.agent_control import Control
    from cutback.application_check_discovery import discover
    from cutback.application_checks import ApplicationCheckContract, git, run_checks, verify
    from cutback.core.workspace import write_private

    original = (root / "app.py").read_bytes()
    control = Control(load_session(root)["endpoint"], api[1]["Authorization"][7:], client=api[0])
    write_private(
        root / ".cutback/agent/campaign.json",
        {
            "campaign_id": row["id"],
            "project_id": tenant[0],
            "workflow": row["workflow"],
            "endpoint": control.endpoint,
        },
    )
    with pytest.raises(ValueError, match="INDEPENDENT_CONFIRMATION_GATE_REQUIRED"):
        proposal(root, control, purpose="owned-real-quality-must-refuse")
    artifact = proposal(root, control, purpose="owned-fixture-campaign-review", sample_only=True)
    assert (
        "_cutback_cached_responses" if repeats else "Owned shorter billing answer."
    ) in artifact["diff"]
    proposed = tmp_path / "reviewed-proposal"
    shutil.copytree(root, proposed, ignore=shutil.ignore_patterns(".cutback", "__pycache__"))
    diff = tmp_path / "review.patch"
    diff.write_text(artifact["diff"], encoding="utf-8", newline="")
    git(proposed, "apply", str(diff))
    git(proposed, "add", ".")
    git(
        proposed,
        "-c",
        "user.name=Owned Synthetic Sample",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "Review campaign change",
    )
    checks = ApplicationCheckContract.model_validate(discover(root, proposed)["contract"])
    receipt = run_checks(checks, root, proposed, execute_local=True)
    assert receipt["all_passed"]
    verify(checks, receipt, root, proposed)
    diff.write_text(artifact["rollback_diff"], encoding="utf-8", newline="")
    git(proposed, "apply", str(diff))
    assert (root / "app.py").read_bytes() == (proposed / "app.py").read_bytes() == original


def test_workload_campaign_rechecks_create_approve_and_each_target_call(
    api, tenant, tmp_path, monkeypatch
):
    import copy
    from decimal import Decimal

    from cutback.sample_app.workload_reuse import fixture_response
    from cutback.server.db import StoredEvent

    _, base, store, row, request = prepared(api, tenant, tmp_path, monkeypatch)
    until = request["specification"]["template"]["workload_portfolio"]["assessment"]["valid_until"]
    assert datetime.fromisoformat(row["expires_at"]) <= datetime.fromisoformat(until)
    with api[2].state.sessions() as session:
        event_id = session.query(StoredEvent).filter_by(project_id=UUID(tenant[0])).first().id
        old_price = session.get(StoredEvent, event_id).price_snapshot_id

    def reprice(changed):
        with api[2].state.sessions.begin() as session:
            session.get(StoredEvent, event_id).price_snapshot_id = (
                "owned-changed-source-price" if changed else old_price
            )

    approval = {"campaign_hash": row["campaign_hash"], "approval_ref": "owned-current-workload"}
    try:
        reprice(True)
        clone = copy.deepcopy(request)
        clone["request_id"] = str(uuid4())
        rejected = api[0].post(base + "/campaigns", headers=api[1], json=clone)
        assert rejected.status_code == 409
        assert rejected.json()["code"] == "WORKLOAD_PORTFOLIO_CURRENT_SOURCE_WINDOW_REQUIRED"
        rejected = api[0].post(
            base + f"/campaigns/{row['id']}/approve", headers=api[1], json=approval
        )
        assert rejected.status_code == 409
        assert rejected.json()["code"] == "CAMPAIGN_CURRENT_AUTHORITY_OR_INPUTS_INVALID"
        reprice(False)
        approve(api, base, row)
        assert advance(api, store, row)["status"] == "WAITING_VALIDATION"
        run_id = child(api, row, "validation")
        calls = []

        def provider(request):
            calls.append(1)
            reprice(True)
            return fixture_response(request)

        executor = OpenAIExecutor(
            http_client=httpx.Client(transport=httpx.MockTransport(provider), trust_env=False)
        )
        executor.synthetic_transport = True
        try:
            result = run_one(
                api[2].state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=run_id,
            )
            assert result["status"] == "FAILED" and len(calls) == 1
            repeated = run_one(
                api[2].state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=run_id,
            )
            assert repeated["status"] == "IDLE" and len(calls) == 1
        finally:
            executor.close()
        with api[2].state.sessions() as session:
            charged = session.get(ExperimentRunRecord, run_id)
            assert charged.requests_used == charged.known_cost_requests == 1
            assert Decimal(charged.spent_usd) > 0
        assert advance(api, store, row)["status"] == "FAILED"
    finally:
        reprice(False)


def test_workload_agent_campaign_reconciles_lost_draft_without_widening_authority(
    api, tenant, tmp_path, monkeypatch
):
    from cutback.agent_campaign import prepare
    from cutback.agent_control import Control
    from cutback.core.workspace import read_private

    root, base, _, _, request = prepared(api, tenant, tmp_path, monkeypatch)
    control = Control(load_session(root)["endpoint"], api[1]["Authorization"][7:], client=api[0])

    class LostDraft:
        endpoint = control.endpoint
        lost = False

        def __call__(self, method, path, body=None):
            result = control(method, path, body)
            if method == "POST" and path.endswith("/draft") and not self.lost:
                self.lost = True
                raise ValueError("OWNED_DRAFT_RESPONSE_LOST")
            return result

    args = dict(
        baseline_snapshot_id=request["specification"]["baseline_snapshot_id"],
        max_usd="10",
        max_requests=1500,
        savings_per_success="0.1",
        forecast_successful_tasks=1000,
        variants_per_round=1,
    )
    interrupted = LostDraft()
    with pytest.raises(ValueError, match="RESPONSE_LOST"):
        prepare(root, interrupted, **args)
    row = prepare(root, interrupted, **args)
    assert prepare(root, interrupted, **args)["id"] == row["id"]
    assert row["requests"] == 0 and row["status"] == "READY_FOR_APPROVAL"
    assert row["specification"]["version"] == "cutback.improvement-campaign.v4"
    assert row["specification"]["generator"]["transport"] == "scripted_test_only"
    saved = root / ".cutback/agent/campaign.json"
    assert control.token not in saved.read_text() and "billing task" not in saved.read_text()
    assert read_private(saved)["campaign_id"] == row["id"]
    with pytest.raises(ValueError, match="REQUIRES_SIMPLE_FIRST"):
        prepare(root, interrupted, **args, simple_first=False)
    assert (
        api[0].post(base + f"/campaigns/{row['id']}/cancel", headers=api[1], json={}).status_code
        == 200
    )
