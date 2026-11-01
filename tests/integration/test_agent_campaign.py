import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from support_agent import prepare_fresh
from test_task_outcomes import declaration, environment, ingest, spans

from cutback.agent_campaign import advance, prepare, status

pytestmark = pytest.mark.postgres


def baseline(api, tenant, workflow):
    item = declaration(
        workflow=workflow,
        components=[{"key": "synthetic-compute", "kind": "compute", "amount_usd": "1"}],
    )
    spans(api, tenant, item)
    assert ingest(api, tenant, item).status_code == 200
    response = api[0].post(
        f"/v1/projects/{tenant[0]}/task-cost-snapshots",
        headers=api[1],
        params={"environment_id": environment(api, tenant[0])},
        json={
            "snapshot_id": str(uuid4()),
            "start": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "end": datetime.now(UTC).isoformat(),
            "workflow": workflow,
            "expected_tasks": 1,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_agent_campaign_reconciles_lost_prepare_and_approval_without_new_identity(
    api, tenant, tmp_path, monkeypatch
):
    root, control, state, _ = prepare_fresh(
        api, tenant, tmp_path, monkeypatch, "classifier", search=True
    )
    snapshot = baseline(api, tenant, state["selected_workflow"])

    class LostAcknowledgement:
        endpoint = control.endpoint
        lost = set()

        def __call__(self, method, path, body=None):
            result = control(method, path, body)
            if method == "POST" and path.endswith(("/draft", "/approve")) and path not in self.lost:
                self.lost.add(path)
                raise ValueError("SYNTHETIC_RESPONSE_ACK_LOST")
            return result

    intermittent = LostAcknowledgement()
    arguments = dict(
        baseline_snapshot_id=snapshot,
        max_usd="10",
        max_requests=1000,
        savings_per_success="0.01",
        forecast_successful_tasks=10000,
    )
    with pytest.raises(ValueError, match="ACK_LOST"):
        prepare(root, intermittent, **arguments)
    row = prepare(root, intermittent, **arguments)
    assert row["status"] == "READY_FOR_APPROVAL"
    assert prepare(root, intermittent, **arguments)["id"] == row["id"]
    with pytest.raises(ValueError, match="EXACT_CAMPAIGN_HASH"):
        advance(root, control, campaign_hash="0" * 64, approval_ref="wrong-hash")
    with pytest.raises(ValueError, match="ACK_LOST"):
        advance(
            root,
            intermittent,
            campaign_hash=row["campaign_hash"],
            approval_ref="synthetic-owner-only",
        )
    assert advance(root, intermittent)["status"] == "QUEUED"
    current = status(root, control)
    assert current["id"] == row["id"] and current["requests"] == 0
    assert current["specification"]["version"] == "cutback.improvement-campaign.v3"
    assert len(current["economic_envelope"]["static_portfolio"]) > 1
    saved = (root / ".cutback/agent/campaign.json").read_text(encoding="utf-8")
    assert (
        "Owned synthetic classification task" not in saved
        and "Owned synthetic AA topic" not in saved
    )
    assert control.token not in saved and json.loads(saved)["campaign_id"] == row["id"]
    with (root / "app.py").open("a", encoding="utf-8") as stream:
        stream.write("\n# changed after approval\n")
    with pytest.raises(ValueError, match="SOURCE_CHANGED"):
        advance(root, control)


def test_old_saved_campaign_without_version_resumes_exact_original_request(
    api,
    tenant,
    tmp_path,
    monkeypatch,
):
    from cutback.core.workspace import read_private, write_private

    root, control, state, _ = prepare_fresh(
        api, tenant, tmp_path, monkeypatch, "classifier", search=True
    )
    arguments = dict(
        baseline_snapshot_id=baseline(api, tenant, state["selected_workflow"]),
        max_usd="10",
        max_requests=1000,
        savings_per_success="0.01",
        forecast_successful_tasks=10000,
    )
    original = prepare(root, control, **arguments, simple_first=False)
    path = root / ".cutback/agent/campaign.json"
    saved = read_private(path)
    saved["request"].pop("version")
    write_private(path, saved)
    resumed = prepare(root, control, **arguments)
    assert resumed["id"] == original["id"]
    assert resumed["campaign_hash"] == original["campaign_hash"]
    assert status(root, control)["specification"]["version"] == "cutback.improvement-campaign.v1"
    assert "version" not in read_private(path)["request"]
    with pytest.raises(ValueError, match="SAVED_CAMPAIGN_SCOPE_CHANGED"):
        prepare(root, control, **arguments, simple_first=True)


def test_agent_campaign_proposes_confirmed_literal_edit_without_modifying_application(
    api, tenant, tmp_path, monkeypatch
):
    from test_campaign_workflow import advance as tick
    from test_campaign_workflow import child, native

    from cutback.agent_campaign import proposal
    from cutback.server.proposal_worker import run_proposal_one
    from cutback.server.worker import run_one

    root, control, state, _ = prepare_fresh(
        api, tenant, tmp_path, monkeypatch, "classifier", search=True
    )
    original = (root / "app.py").read_bytes()
    row = prepare(
        root,
        control,
        baseline_snapshot_id=baseline(api, tenant, state["selected_workflow"]),
        max_usd="10",
        max_requests=1000,
        savings_per_success="0.01",
        forecast_successful_tasks=10000,
        simple_first=False,
    )
    assert row["instruction_edit_qualified"]
    advance(root, control, campaign_hash=row["campaign_hash"], approval_ref="synthetic-fixtures")
    store = api[2].state.artifact_store
    executor, requests = native(monkeypatch, dynamic_seeds=True)
    try:
        assert tick(api, store, row)["status"] == "WAITING_PROPOSAL"
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
        assert tick(api, store, row)["status"] == "WAITING_VALIDATION"
        for phase, expected in (
            ("validation", "WAITING_CONFIRMATION"),
            ("confirmation", "COMPLETED"),
        ):
            result = run_one(
                api[2].state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=child(api, row, phase),
            )
            assert result["status"] == "COMPLETED", result
            assert tick(api, store, row)["status"] == expected
        with pytest.raises(ValueError, match="INDEPENDENT_CONFIRMATION_GATE_REQUIRED"):
            proposal(root, control, purpose="owned-synthetic-change-review")
        artifact = proposal(
            root, control, purpose="owned-synthetic-change-review", sample_only=True
        )
        assert "Return A only." in artifact["diff"]
        assert "Owned synthetic classification task" in artifact["rollback_diff"]
        assert artifact["application_checks_required"] and artifact["confirmation_gate"]["passed"]
        assert (root / "app.py").read_bytes() == original
        checkpoint = (root / ".cutback/agent/campaign.json").read_text(encoding="utf-8")
        assert "Return A only." not in checkpoint and control.token not in checkpoint
        calls = len(requests)
        assert (
            proposal(root, control, purpose="repeat-review", sample_only=True)["diff_sha256"]
            == artifact["diff_sha256"]
        )
        assert len(requests) == calls
        import shutil
        import subprocess

        from cutback.agent_campaign import review
        from cutback.application_checks import ApplicationCommand, run_checks
        from cutback.application_checks import prepare as prepare_checks

        roots = []
        for name in ("base", "proposed"):
            tree = tmp_path / name
            if name == "base":
                tree.mkdir()
                (tree / "app.py").write_bytes(original)
                for args in (("init",), ("config", "core.autocrlf", "false"), ("add", "app.py")):
                    subprocess.run(["git", "-C", str(tree), *args], check=True, capture_output=True)
            else:
                shutil.copytree(roots[0], tree)
            if name == "proposed":
                applied = subprocess.run(
                    ["git", "-C", str(tree), "apply", "-"],
                    input=artifact["diff"].encode(),
                    capture_output=True,
                )
                assert applied.returncode == 0
                subprocess.run(
                    ["git", "-C", str(tree), "add", "app.py"], check=True, capture_output=True
                )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(tree),
                    "-c",
                    "user.name=Synthetic",
                    "-c",
                    "user.email=synthetic@example.invalid",
                    "commit",
                    "-m",
                    "Owned classifier",
                ],
                check=True,
                capture_output=True,
            )
            roots.append(tree)
        check_contract = prepare_checks(
            *roots,
            [
                ApplicationCommand(
                    key="classifier-request-contract",
                    arguments=[
                        "-c",
                        "import ast; from pathlib import Path; "
                        "t=ast.parse(Path('app.py').read_text(encoding='utf-8')); "
                        "c=[n for n in ast.walk(t) if isinstance(n, ast.Call) and "
                        "isinstance(n.func, ast.Attribute) and n.func.attr=='create']; "
                        "assert len(c)==1; k={v.arg:v.value for v in c[0].keywords}; "
                        "assert set(k)=={'model','input','instructions','max_output_tokens'}; "
                        "assert 16 <= k['max_output_tokens'].value <= 64; "
                        "assert isinstance(k['instructions'].value,str)",
                    ],
                )
            ],
        )
        check_receipt = run_checks(check_contract, *roots, execute_local=True)
        reviewed = review(
            root,
            control,
            purpose="application-check-review",
            sample_only=True,
            contract=check_contract,
            receipt=check_receipt,
            base=roots[0],
            proposed=roots[1],
            coverage={w: ["classifier-request-contract"] for w in artifact["affected_workflows"]},
            branch="cutback/owned-classifier-review",
        )
        assert reviewed["status"] == "READY_FOR_PR_REVIEW"
        assert reviewed["evidence_scope"] == "sample_only" and not reviewed["remote_published"]
        assert len(requests) == calls and (root / "app.py").read_bytes() == original
        from support_review_publication import OwnedGitHub, api_for

        from cutback.agent_publication import publish_review
        from cutback.core.workspace import read_private
        from cutback.review_publication import prepare_publication

        publication_options = {
            "purpose": "application-pr-review",
            "repository": "example-owner/task-app",
            "base_branch": "main",
            "head_branch": "cutback/owned-classifier-review",
        }
        # Default is a local preview: no GitHub credential or transport required.
        monkeypatch.delenv("CUTBACK_GITHUB_TOKEN", raising=False)
        preview = publish_review(root, control, **publication_options)
        assert preview["status"] == "PREVIEW_ONLY_EXPLICIT_PUBLICATION_REQUIRED"
        assert not preview["remote_published"] and preview["provider_calls"] == 0
        prepared_publication = prepare_publication(
            reviewed,
            artifact,
            *roots,
            **{key: value for key, value in publication_options.items() if key != "purpose"},
        )
        remote = OwnedGitHub(prepared_publication, lose="/pulls")
        github = api_for(remote)
        try:
            with pytest.raises(ValueError, match="REMOTE_OUTCOME_UNCERTAIN"):
                publish_review(root, control, **publication_options, publish=True, api=github)
            pending_publication = read_private(root / ".cutback/agent/campaign-publication.json")
            assert pending_publication["dispatched"][-1] == "pull_request"
            assert "result" not in pending_publication and len(remote.pulls) == 1
            published = publish_review(
                root, control, **publication_options, publish=True, api=github
            )
            repeated = publish_review(
                root, control, **publication_options, publish=True, api=github
            )
            assert published == repeated and published["draft"]
            assert published["proposed_commit"] == check_contract.proposed.commit
            assert len(remote.posts) == 5 and len(remote.pulls) == 1
            # A source change cannot reuse the completed review for another upload.
            checked_bytes = (roots[1] / "app.py").read_bytes()
            (roots[1] / "app.py").write_bytes(b"# owned subsequent unreviewed change\n")
            before_remote = len(remote.requests)
            with pytest.raises(ValueError, match="APPLICATION_CHECK"):
                publish_review(root, control, **publication_options, publish=True, api=github)
            assert len(remote.requests) == before_remote
            (roots[1] / "app.py").write_bytes(checked_bytes)
        finally:
            github.close()
        publication_journal = read_private(root / ".cutback/agent/campaign-publication.json")
        assert publication_journal["result"] == published
        assert "synthetic-github-test-token" not in json.dumps(publication_journal)
        assert len(requests) == calls and (root / "app.py").read_bytes() == original
        from support_campaign_outcomes import assert_campaign_outcome_link

        assert_campaign_outcome_link(
            api, tenant, root, control, artifact, state["selected_workflow"]
        )
        assert len(requests) == calls and (root / "app.py").read_bytes() == original
        selected_url = f"/v1/projects/{tenant[0]}/campaigns/{row['id']}/selected/content"
        assert (
            api[0].post(selected_url, headers=tenant[2], json={"purpose": "denied"}).status_code
            == 403
        )
        assert api[0].post(selected_url, headers=api[1], json={}).status_code == 422
        from uuid import UUID

        from cutback.server.db import RecommendationRecord

        with api[2].state.sessions.begin() as session:
            session.get(
                RecommendationRecord, UUID(artifact["recommendation_id"])
            ).status = "rejected"
        assert (
            api[0].post(selected_url, headers=api[1], json={"purpose": "rejected"}).status_code
            == 409
        )
    finally:
        executor.close()
