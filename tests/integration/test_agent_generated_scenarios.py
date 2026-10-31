"""Lost HTTP replies keep one preparation, one native call and one reviewed import."""

from uuid import UUID

import pytest
from sqlalchemy import func, select
from test_generated_scenario_workflow import generate
from test_generated_scenario_workflow import prepare as fixture

from cutback.agent_control import Control
from cutback.agent_scenarios import advance, prepare, read, review_and_import
from cutback.server.scenario_db import ScenarioDatasetImport, ScenarioVersionRecord

pytestmark = pytest.mark.postgres


class InterruptedControl:
    def __init__(self, control):
        self.control = control
        self.endpoint = control.endpoint
        self.fail_suffix = None
        self.calls = []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path))
        result = self.control(method, path, body)
        if method == "POST" and self.fail_suffix and path.endswith(self.fail_suffix):
            self.fail_suffix = None
            raise ValueError("OWNED_SYNTHETIC_REPLY_LOST")
        return result


def test_agent_scenario_workflow_recovers_draft_queue_and_import_without_billable_replay(
    api, tenant, tmp_path, monkeypatch
):
    _, store, request = fixture(api, tenant, tmp_path, monkeypatch)
    root = tmp_path / "independent-agent"
    root.mkdir()
    control = InterruptedControl(
        Control("http://127.0.0.1:8080", api[1]["Authorization"][7:], client=api[0])
    )
    request.pop("request_id")
    control.fail_suffix = "/drafts"
    with pytest.raises(ValueError, match="REPLY_LOST"):
        prepare(root, control, tenant[0], request)
    planned = prepare(root, control, tenant[0], request)
    assert planned["phase"] == "PLANNED"
    assert planned["next_action"]["automatic_billable_retry"] is False
    with api[2].state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ScenarioVersionRecord)
                .where(ScenarioVersionRecord.project_id == UUID(tenant[0]))
            )
            == 1
        )
    with pytest.raises(ValueError, match="EXACT_BUDGET_APPROVAL"):
        advance(root, control, tenant[0])
    control.fail_suffix = "/runs"
    with pytest.raises(ValueError, match="REPLY_LOST"):
        advance(
            root,
            control,
            tenant[0],
            plan_hash=planned["generation"]["plan_hash"],
            approval_ref="synthetic-owner-exact-budget",
        )
    calls = len(control.calls)
    queued = advance(root, control, tenant[0])
    assert queued["phase"] == "QUEUED"
    assert all(method == "GET" for method, _ in control.calls[calls:])
    result, native = generate(api, store, queued["generation"])
    assert result["status"] == "COMPLETED" and len(native) == 1
    ready = advance(root, control, tenant[0])
    assert ready["phase"] == "REVIEW_REQUIRED"
    with pytest.raises(ValueError, match="PURPOSE_READ_VERSION"):
        review_and_import(
            root,
            control,
            tenant[0],
            version_sha256=ready["generation"]["version_sha256"],
            decision="approve",
            approval_ref="synthetic-owner-reviewed",
        )
    content = read(root, control, tenant[0], purpose="synthetic-owner-read")
    private_metadata = (root / ".cutback/scenario-agent/session.json").read_text("utf-8")
    assert request["requirements"]["task_brief"] not in private_metadata
    assert "Please keep the response concise." not in private_metadata
    control.fail_suffix = "/imports"
    with pytest.raises(ValueError, match="REPLY_LOST"):
        review_and_import(
            root,
            control,
            tenant[0],
            version_sha256=content["version_sha256"],
            decision="approve",
            approval_ref="synthetic-owner-reviewed",
        )
    imported = review_and_import(
        root,
        control,
        tenant[0],
        version_sha256=content["version_sha256"],
        decision="approve",
        approval_ref="synthetic-owner-reviewed",
    )
    assert imported["phase"] == "IMPORTED" and imported["development_dataset"]["existing_import"]
    assert advance(root, control, tenant[0])["phase"] == "IMPORTED"
    with api[2].state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ScenarioDatasetImport)
                .where(ScenarioDatasetImport.project_id == UUID(tenant[0]))
            )
            == 1
        )
    assert "test_evidence" not in imported["development_dataset"]


def test_scenario_workflow_refuses_changed_collector_request_or_unread_review(
    api, tenant, tmp_path, monkeypatch
):
    _, _, request = fixture(api, tenant, tmp_path, monkeypatch)
    root = tmp_path / "independent-agent"
    root.mkdir()
    control = InterruptedControl(
        Control("http://127.0.0.1:8080", api[1]["Authorization"][7:], client=api[0])
    )
    planned = prepare(root, control, tenant[0], request)
    count = len(control.calls)
    changed = {**request, "max_training_examples": 1}
    with pytest.raises(ValueError, match="ORIGINAL_REQUEST"):
        prepare(root, control, tenant[0], changed)
    assert len(control.calls) == count
    control.endpoint = "http://127.0.0.1:8081"
    with pytest.raises(ValueError, match="DESTINATION_OR_ROOT"):
        advance(
            root,
            control,
            tenant[0],
            plan_hash=planned["generation"]["plan_hash"],
            approval_ref="synthetic",
        )
    assert len(control.calls) == count
