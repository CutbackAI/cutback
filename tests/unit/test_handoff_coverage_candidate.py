"""Coverage drafts remove JSON assembly, not source/command review or execution consent."""

from copy import deepcopy

import pytest
from test_agent_campaign_handoff_controls import invoke
from test_agent_campaign_handoff_controls import journey as journey

from cutback.application_checks import ApplicationCommand, snapshot
from cutback.application_handoff import declared_coverage_candidate
from cutback.core.workspace import read_private, write_private


def discovery():
    return {
        "impact": {
            "mode": "all_commands_affected_first",
            "fallback_reasons": [],
            "workflows": [
                {"key": "classify", "status": "affected", "commands": ["application"]},
                {"key": "route", "status": "affected", "commands": ["application"]},
            ],
        }
    }


def commands():
    return [ApplicationCommand(key="application", arguments=["check_application.py"])]


def test_exact_reviewed_declaration_drafts_all_required_consumers():
    candidate = declared_coverage_candidate(discovery(), commands(), ["route", "classify"])
    assert candidate == {"classify": ["application"], "route": ["application"]}


@pytest.mark.parametrize("expected", [None, [], ["absent"], ["classify", "classify"], [1]])
def test_missing_unmatched_or_invalid_required_workflows_do_not_invent_a_map(expected):
    assert declared_coverage_candidate(discovery(), commands(), expected) is None


@pytest.mark.parametrize(
    "mutation", ["fallback", "unchanged", "missing-command", "unsupported-mode"]
)
def test_unknown_impact_and_unchecked_consumers_do_not_get_coverage_drafts(mutation):
    report = deepcopy(discovery())
    if mutation == "fallback":
        report["impact"]["fallback_reasons"] = ["dynamic_python_execution_or_import"]
    elif mutation == "unchanged":
        report["impact"]["workflows"][0]["status"] = "unchanged_declared_scope"
    elif mutation == "missing-command":
        report["impact"]["workflows"][0]["commands"] = ["unchecked"]
    else:
        report["impact"]["mode"] = "all_commands_fallback"
    assert declared_coverage_candidate(report, commands(), ["classify", "route"]) is None


@pytest.mark.parametrize("journey", ["mapped"], indirect=True)
def test_real_handoff_prepares_draft_requires_review_and_runs_once(journey, monkeypatch):
    import cutback.agent_campaign_handoff as handoff

    root, destination, *_ = journey
    original = snapshot(root)
    actual, runs = handoff.run_checks, []

    def checked(*args, **kwargs):
        runs.append(True)
        return actual(*args, **kwargs)

    monkeypatch.setattr(handoff, "run_checks", checked)
    preview = invoke(journey)
    assert preview["status"] == "CHECKS_READY" and not runs
    assert preview["owner_consumer_coverage_required"]
    assert (
        preview["coverage_candidate_trust"]
        == "tracked_workflow_declaration_requires_explicit_review"
    )
    candidate = read_private(destination / "coverage-candidate.json")
    assert candidate == {"classify": ["application"], "route": ["application"]}
    assert read_private(destination / "discovery.json")["application_commands_executed"] == 0
    assert invoke(journey) == preview and not runs
    with pytest.raises(ValueError, match="OWNER_CONSUMER_COVERAGE_REQUIRED"):
        invoke(journey, execute_local=True)
    assert not runs
    final = invoke(journey, coverage=candidate, execute_local=True)
    assert final["status"] == "REVIEWED" and runs == [True]
    assert invoke(journey, coverage=candidate, execute_local=True)["status"] == "REVIEWED"
    assert runs == [True] and snapshot(root) == original


@pytest.mark.parametrize("journey", ["mapped"], indirect=True)
@pytest.mark.parametrize("fault", ["changed", "missing", "malformed"])
def test_retained_coverage_draft_cannot_change_silently(journey, fault):
    invoke(journey)
    path = journey[1] / "coverage-candidate.json"
    if fault == "changed":
        write_private(path, {"classify": ["application"]})
    elif fault == "missing":
        path.unlink()
    else:
        path.write_text("invalid-json", encoding="utf-8")
    with pytest.raises(ValueError, match="COVERAGE_CANDIDATE_CHANGED"):
        invoke(journey)


def test_ordinary_handoff_without_a_mapping_keeps_existing_review_requirement(journey):
    preview = invoke(journey)
    assert preview["coverage_candidate_file"] is None
    assert preview["owner_consumer_coverage_required"]
    assert not (journey[1] / "coverage-candidate.json").exists()
