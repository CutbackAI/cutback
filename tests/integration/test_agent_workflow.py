import json

import httpx
import pytest
from support_agent import native_response, prepare_fresh

from cutback.agent import advance, load_session, prepare, proposal
from cutback.evals.executors import OpenAIExecutor
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("kind", ["classifier", "extractor", "shared"])
def test_fresh_app_agent_plan_native_worker_bound_patch_without_demo(
    api, tenant, tmp_path, monkeypatch, kind
):
    root, control, state, args = prepare_fresh(api, tenant, tmp_path, monkeypatch, kind)
    assert state["phase"] == "PLAN_READY" and state["plan"]["paid_calls_made"] == 0
    assert state["plan"]["fits_budget"] and state["plan"]["requests"] <= 1000
    assert prepare(root, control, tenant[0], **args)["plan"] == state["plan"]
    metadata = json.dumps(load_session(root))
    assert (
        "Owned synthetic classification task" not in metadata
        and "Owned synthetic AA topic" not in metadata
    )
    with pytest.raises(ValueError, match="EXACT_PLAN_HASH"):
        advance(root, control, plan_hash="a" * 64, approval_ref="wrong-plan")
    with pytest.raises(ValueError):
        advance(
            root, control, plan_hash=state["plan"]["plan_hash"], approval_ref="invalid reference"
        )
    assert load_session(root)["phase"] == "PLAN_READY"
    queued = advance(
        root,
        control,
        plan_hash=state["plan"]["plan_hash"],
        approval_ref="synthetic-transports-only",
    )
    assert advance(root, control)["run_id"] == queued["run_id"]
    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "synthetic-never-provider")
    executor = OpenAIExecutor(
        http_client=httpx.Client(transport=httpx.MockTransport(native_response))
    )
    try:
        outcome = run_one(
            api[2].state.sessions,
            api[2].state.artifact_store,
            executor=executor,
            execution_enabled=True,
            only_run_id=queued["run_id"],
        )
    finally:
        executor.close()
    assert outcome["status"] == "COMPLETED", outcome
    completed = advance(root, control)
    assert completed["phase"] == "COMPLETED" and completed["report"]["requests"] > 0
    assert completed["provider_calls"] is None
    before = (root / "app.py").read_bytes()
    artifact = proposal(root, control, purpose="owned-synthetic-review")
    assert artifact["review_status"] == "human_review_required"
    assert "gpt-4.1-mini" in artifact["diff"] and "gpt-4.1" in artifact["rollback_diff"]
    assert artifact["source_file"] == ("config.py" if kind == "shared" else "app.py")
    assert (root / "app.py").read_bytes() == before
    assert load_session(root)["phase"] == "PROPOSAL_READY"
    (root / "app.py").write_bytes(before + b"\n# modified\n")
    with pytest.raises(ValueError, match="SOURCE_CHANGED"):
        proposal(root, control, purpose="stale-review")


@pytest.mark.parametrize("unrelated_runs", [0, 1001])
def test_lost_queue_response_reconciles_same_run_and_never_posts_twice(
    api, tenant, tmp_path, monkeypatch, unrelated_runs
):
    root, control, state, _ = prepare_fresh(api, tenant, tmp_path, monkeypatch)

    class LostResponse:
        endpoint = control.endpoint
        queues = 0
        lookups = 0

        def __call__(self, method, path, body=None):
            if method == "GET" and path.endswith("/execution"):
                self.lookups += 1
            result = control(method, path, body)
            if method == "POST" and path.endswith("/experiments"):
                self.queues += 1
                raise ConnectionError("synthetic lost reply")
            return result

    lost = LostResponse()
    with pytest.raises(ConnectionError):
        advance(root, lost, plan_hash=state["plan"]["plan_hash"], approval_ref="synthetic-only")
    assert load_session(root)["phase"] == "QUEUEING"
    if unrelated_runs:
        from uuid import UUID, uuid4

        from cutback.server.db import ExperimentPlanRecord, ExperimentRunRecord

        # Newer unrelated history must not hide this exact lost-acknowledgement run.
        with api[2].state.sessions.begin() as session:
            source = session.get(ExperimentPlanRecord, UUID(state["plan"]["id"]))
            plans = [
                ExperimentPlanRecord(
                    id=uuid4(),
                    project_id=UUID(tenant[0]),
                    dataset_id=source.dataset_id,
                    specification={"public_specification": {}},
                )
                for _ in range(unrelated_runs)
            ]
            session.add_all(plans)
            session.flush()
            session.add_all(
                ExperimentRunRecord(
                    project_id=UUID(tenant[0]),
                    plan_id=plan.id,
                    status="CANCELLED",
                    results={},
                )
                for plan in plans
            )
    recovered = advance(root, lost)
    assert recovered["phase"] == "QUEUED" and lost.queues == 1 and lost.lookups == 1
    assert advance(root, lost)["run_id"] == recovered["run_id"] and lost.queues == 1
    control("POST", f"/v1/projects/{tenant[0]}/experiments/{recovered['run_id']}/cancel", {})


@pytest.mark.parametrize("failure", ["before_approval", "after_approval", "before_queue"])
def test_unknown_approval_or_queue_never_replays_when_exact_run_is_absent(
    api, tenant, tmp_path, monkeypatch, failure
):
    root, control, state, _ = prepare_fresh(api, tenant, tmp_path, monkeypatch)

    class Interrupted:
        endpoint = control.endpoint

        def __init__(self):
            self.calls = []

        def __call__(self, method, path, body=None):
            self.calls.append((method, path))
            if method == "POST":
                if path.endswith("/approve"):
                    if failure == "before_approval":
                        raise ConnectionError("synthetic interruption")
                    result = control(method, path, body)
                    if failure == "after_approval":
                        raise ConnectionError("synthetic lost approval reply")
                    return result
                if path.endswith("/experiments"):
                    raise ConnectionError("synthetic interruption before queue commit")
            return control(method, path, body)

    interrupted = Interrupted()
    with pytest.raises(ConnectionError):
        advance(
            root,
            interrupted,
            plan_hash=state["plan"]["plan_hash"],
            approval_ref="synthetic-interruption-only",
        )
    before = len(interrupted.calls)
    for _ in range(2):
        result = advance(root, interrupted)
        assert result["stop_code"] == "UNCERTAIN_APPROVAL_OR_QUEUE_RECONCILE_WITH_OPERATOR"
        assert "run_id" not in result
    assert all(
        method == "GET" and path.endswith("/execution")
        for method, path in interrupted.calls[before:]
    )
    if failure != "before_approval":
        # Simulate the original request committing after the first reconciliation.
        # This direct call stands in for the delayed server handler, not a CLI retry.
        queued = control(
            "POST",
            f"/v1/projects/{tenant[0]}/experiments",
            {"plan_id": state["plan"]["id"]},
        )
        resumed = advance(root, interrupted)
        assert resumed["run_id"] == queued["id"] and resumed["phase"] == "QUEUED"
        assert "stop_code" not in resumed
        assert all(method == "GET" for method, _ in interrupted.calls[before:])
        control("POST", f"/v1/projects/{tenant[0]}/experiments/{queued['id']}/cancel", {})


def test_source_plan_requires_registered_profile_and_viewer_cannot_read_prompt(
    api, tenant, tmp_path, monkeypatch
):
    from uuid import UUID

    from sqlalchemy import select

    from cutback.agent_control import Control
    from cutback.server.db import ScanRun

    root, control, state, _ = prepare_fresh(api, tenant, tmp_path, monkeypatch)
    base = f"/v1/projects/{tenant[0]}"
    content = control(
        "POST",
        base + f"/experiments/plans/{state['plan']['id']}/content",
        {"purpose": "synthetic-profile-check"},
    )
    with api[2].state.sessions.begin() as session:
        for row in session.scalars(select(ScanRun).where(ScanRun.project_id == UUID(tenant[0]))):
            row.inventory = {**row.inventory, "request_profiles": []}
    with pytest.raises(ValueError, match="SOURCE_REQUEST_PROFILE_NOT_REGISTERED"):
        control(
            "POST",
            base + "/experiments/plans",
            {"dataset_id": state["dataset_id"], "specification": content["specification"]},
        )
    viewer = Control(control.endpoint, tenant[2]["Authorization"][7:], client=api[0])
    with pytest.raises(ValueError):
        viewer(
            "POST",
            base + f"/experiments/plans/{state['plan']['id']}/content",
            {"purpose": "forbidden-viewer-read"},
        )
    with pytest.raises(ValueError):
        viewer("GET", f"/v1/projects/{UUID(int=1)}/experiments/plans")
