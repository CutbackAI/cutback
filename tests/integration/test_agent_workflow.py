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


def test_lost_queue_response_reconciles_same_run_and_never_posts_twice(
    api, tenant, tmp_path, monkeypatch
):
    root, control, state, _ = prepare_fresh(api, tenant, tmp_path, monkeypatch)

    class LostResponse:
        endpoint = control.endpoint
        queues = 0

        def __call__(self, method, path, body=None):
            result = control(method, path, body)
            if method == "POST" and path.endswith("/experiments"):
                self.queues += 1
                raise ConnectionError("synthetic lost reply")
            return result

    lost = LostResponse()
    with pytest.raises(ConnectionError):
        advance(root, lost, plan_hash=state["plan"]["plan_hash"], approval_ref="synthetic-only")
    assert load_session(root)["phase"] == "QUEUEING"
    recovered = advance(root, lost)
    assert recovered["phase"] == "QUEUED" and lost.queues == 1
    assert advance(root, lost)["run_id"] == recovered["run_id"] and lost.queues == 1
    control("POST", f"/v1/projects/{tenant[0]}/experiments/{recovered['run_id']}/cancel", {})


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
