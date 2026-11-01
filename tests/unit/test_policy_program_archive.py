"""New revisions cannot inherit approval or discard an unresolved earlier bill."""

from uuid import uuid4

import pytest

from cutback.agent_policy_program import CONTRACT, archive
from cutback.core.request_profiles import fingerprint
from cutback.core.workspace import read_private, write_private


def saved_program(tmp_path, *, complete=True, terminal=True):
    root = tmp_path / "application"
    root.mkdir()
    identifier, template = str(uuid4()), str(uuid4())
    source = {
        key: "owned"
        for key in (
            "producer_file",
            "configuration_file",
            "commit",
            "source_tree_hash",
            "workflow",
            "max_input_bytes",
        )
    }
    saved = {
        "version": CONTRACT,
        "endpoint": "http://127.0.0.1:8080",
        "root_fingerprint": fingerprint(str(root.resolve())),
        "program_id": identifier,
        "project_id": str(uuid4()),
        "program_hash": "a" * 64,
        "template_plan_id": template,
        "source": source,
    }
    path = root / ".cutback/agent/policy-program.json"
    path.parent.mkdir(parents=True)
    write_private(path, saved)

    class Collector:
        endpoint = saved["endpoint"]

        def __call__(self, method, url, body=None):
            assert method == "GET" and url.endswith(identifier) and body is None
            return {
                "id": identifier,
                "program_hash": saved["program_hash"],
                "template_plan_id": template,
                "status": "FAILED" if terminal else "QUEUED",
                "billing_complete": complete,
                "requests": 1,
                "known_cost_requests": 1 if complete else 0,
                "specification": {"template": {"source_policy_execution": {"source": source}}},
            }

    return root, path, saved, Collector()


@pytest.mark.parametrize("complete,terminal", [(False, True), (True, False)])
def test_unsettled_or_active_program_cannot_be_archived(tmp_path, complete, terminal):
    root, path, saved, control = saved_program(tmp_path, complete=complete, terminal=terminal)
    with pytest.raises(ValueError, match="TERMINAL_KNOWN_ACCOUNTING_REQUIRED"):
        archive(root, control)
    assert read_private(path) == saved


def test_terminal_known_program_is_retained_before_starting_a_new_source_revision(tmp_path):
    root, path, saved, control = saved_program(tmp_path)
    (root / "app.py").write_text("# A new source revision\n", encoding="utf-8")
    receipt = archive(root, control)
    assert receipt["status"] == "ARCHIVED" and receipt["provider_calls"] == 0
    assert receipt["approval_transferred"] is False and not path.exists()
    assert (
        read_private(path.with_name("policy-program-archived-" + saved["program_id"] + ".json"))
        == saved
    )
