"""Offline application preflight; source metadata is neither permission nor coverage."""

import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from cutback.core.request_profiles import fingerprint
from cutback.core.sdk_compat import OPENAI_OBSERVER_SDKS
from cutback.core.source_guidance import explain_source

LIMIT = 20


def command(code, summary, arguments, *, requires=()):
    return {
        "code": code,
        "summary": summary,
        "argv": arguments,
        "working_directory": "application_root",
        "requires": list(requires),
        "automatic_execution": False,
        "provider_calls_authorized": 0,
    }


def tool_dependencies():
    """Inspect distribution metadata, never import providers or infer the app's environment."""
    found = {}
    for name in ("openai", "anthropic", "httpx", "fastapi", "psycopg"):
        try:
            value = version(name)
        except PackageNotFoundError:
            value = None
        # Package metadata is untrusted too; never forward arbitrary strings.
        safe = value is None or (
            len(value) <= 40 and all(c in "0123456789abcdefghijklmnopqrstuvwxyz.+-" for c in value)
        )
        found[name] = value if safe else "unrecognized_version_metadata"
    return {
        "scope": "current_cutback_tool_interpreter_not_application_environment",
        "installed_versions": found,
        "qualified_observer_versions": {
            "openai": sorted(OPENAI_OBSERVER_SDKS),
            "anthropic": ["2.3.0"],
        },
        "application_sdk_versions": None,
        "application_dependency_changes_performed": False,
        "next_action": "Inspect the application's pinned installed SDK before observation; "
        "preserve its pins and use a qualified adapter rather than silently upgrading it.",
    }


def check_readiness(root):
    """Use the same guarded Git plumbing and parsers as application-check discovery."""
    from cutback.application_check_discovery import candidates
    from cutback.application_checks import snapshot

    try:
        tree = snapshot(root)
        commands, origins, unknowns = candidates(root, tree)
        if snapshot(root) != tree:
            raise ValueError("APPLICATION_CHECK_DISCOVERY_SOURCE_CHANGED")
    except (OSError, ValueError, SyntaxError, TypeError, UnicodeError):
        return {
            "status": "SOURCE_OR_CHECK_DECLARATION_REVIEW_REQUIRED",
            "candidate_commands": 0,
            "source_tree_hash": None,
            "unknowns": ["clean_exact_git_root_and_bounded_check_declarations_not_verified"],
            "next_action": "Review the repository root, tracked changes, unsafe paths and check "
            "declarations. Preserve your work; do not reset or discard changes. Rerun diagnosis "
            "from the intended clean application root when ready.",
            "commands_executed": 0,
            "workflow_coverage": "unknown",
        }
    return {
        "status": "CHECK_CANDIDATES_NEED_REVIEW" if commands else "NO_CHECK_CANDIDATES",
        "candidate_commands": len(commands),
        "candidate_runtimes": sorted({item.runtime for item in commands}),
        "candidate_origins": sorted(set(origins.values())),
        "source_tree_hash": tree.tree_hash,
        "unknowns": sorted(
            set(unknowns) | {"workflow_test_coverage_not_independently_established"}
        ),
        "next_action": "Review candidate commands, dependencies and side effects; use cutback "
        "checks discover on independent base/proposed trees before explicit local execution."
        if commands
        else "Add or explicitly declare a bounded application check that detects a known bad "
        "change; a source adapter alone does not establish regression coverage.",
        "commands_executed": 0,
        "workflow_coverage": "unknown",
    }


def summarize(state, *, checks, dependencies):
    """A compact decision report. No credentials, fixture bodies, prompt text or shell snippets."""
    guide = explain_source(state)
    choices = {
        item["workflow_id"]: set(item["modes"]) for item in state.get("preparation_options", [])
    }
    policies = {item["workflow_id"] for item in state.get("policy_producers", [])}
    for workflow in policies:
        # Preserve an inspected wrapper, never recommend replaying its inner call.
        choices[workflow] = {"source_policy_program"}
    fresh = state.get("source_status") != "CHANGED_RESCAN_AFTER_RUN"
    can_prepare = fresh and state["phase"] in {"ANALYZED", "SOURCE_CHANGED"}
    actions = []
    if not can_prepare:
        readiness = "CONTINUE_EXISTING_WORKFLOW"
        actions.append(
            command(
                "INSPECT_EXISTING_INTENT",
                "Reconcile the existing run before starting another.",
                ["cutback", "workflow", "status", "--target", "."],
            )
        )
    elif not choices:
        readiness = "SOURCE_ADAPTER_REQUIRED"
        actions.append(
            command(
                "EXPLAIN_SOURCE_BLOCKERS",
                "Review exact request blockers and preserve application behavior.",
                ["cutback", "workflow", "explain", "--target", "."],
            )
        )
    elif len(choices) > 1 or any(len(modes) != 1 for modes in choices.values()):
        readiness = "SELECT_WORKFLOW_OR_ADAPTER"
        for workflow in sorted(choices)[:LIMIT]:
            actions.append(
                command(
                    "SELECT_SOURCE_WORKFLOW",
                    "Narrow discovery to one workflow; no request is executed.",
                    ["cutback", "optimize", ".", "--workflow", workflow, "--json"],
                )
            )
    else:
        readiness = "REVIEW_PREPARATION_INPUTS"
        policy = bool(policies)
        actions.append(
            command(
                "INSPECT_PREPARATION_CONTRACT",
                "Inspect the exact nonbillable preparation inputs.",
                ["cutback", "source-policy" if policy else "workflow", "prepare", "--help"],
                requires=(
                    "permitted_data_and_rights_reference",
                    "approved_collector_and_current_project_rights",
                    "quality_definition_and_untouched_confirmation_groups",
                    "explicit_request_and_decimal_usd_ceiling",
                ),
            )
        )
    startup = state.get("instrumentation", {}).get("proposals", [])
    fixed_consumers = sorted(
        {item["source"]["producer_file"] for item in state.get("policy_producers", [])}
    )
    startup_actions = []
    for entry in startup[:LIMIT]:
        if entry.get("status") == "PLAN_ONLY" and entry["source_file"] not in fixed_consumers:
            startup_actions.append(
                command(
                    "REVIEW_STARTUP_PROPOSAL",
                    "Inspect a proposal after confirming the actual process startup.",
                    [
                        "cutback",
                        "instrument",
                        "--target",
                        ".",
                        "--entrypoint",
                        entry["source_file"],
                        "--plan",
                        "--json",
                    ],
                )
            )
    report = {
        "version": "cutback.application-readiness.v1",
        "readiness": readiness,
        "phase": state["phase"],
        "analysis_sha256": state["analysis_sha256"],
        "source_freshness": "fresh_bounded_discovery"
        if fresh
        else "saved_active_intent_source_has_changed",
        "workflow_choices": [
            {"workflow_id": workflow, "modes": sorted(modes)}
            for workflow, modes in sorted(choices.items())[:LIMIT]
        ],
        "omitted_workflow_choices": max(0, len(choices) - LIMIT),
        "source_contracts": guide,
        "application_checks": checks,
        "dependencies": dependencies,
        "startup": {
            "proposal_statuses": sorted({entry["status"] for entry in startup}),
            "fixed_policy_consumers": [
                {"source_file": name, "status": "EXTERNAL_PROCESS_STARTUP_REQUIRED"}
                for name in fixed_consumers[:LIMIT]
            ],
            "omitted_fixed_consumers": max(0, len(fixed_consumers) - LIMIT),
            "review_commands": startup_actions,
            "omitted_proposals": max(0, len(startup) - LIMIT),
            "startup_coverage": "requires_inspection_of_actual_process_and_handlers",
            "source_applied": False,
        },
        "data": {
            "discovered_fixture_candidates": len(state.get("fixtures", [])),
            "rights_verified": False,
            "content_uploaded": False,
            "independent_quality_verified": False,
        },
        "next_actions": actions,
        "collector": {"contacted": False, "authorization_verified": False},
        "provider_calls": 0,
        "automatic_billable_retry": False,
        "application_commands_executed": 0,
        "application_source_changed": False,
        "private_journal": ".cutback/agent/session.json",
        "coverage": "bounded_static_discovery_not_runtime_or_complete_application_coverage",
    }
    return {**report, "report_sha256": fingerprint(report)}


def diagnose(root: Path, *, workflow=None):
    from cutback.agent import analyze
    from cutback.application_checks import source_root

    root = source_root(root)
    state = analyze(root, workflow=workflow)
    return summarize(state, checks=check_readiness(root), dependencies=tool_dependencies())


def render(report):
    guide = report["source_contracts"]
    lines = [
        "Cutback application readiness",
        report["readiness"],
        "",
        guide["discovery_note"],
        f"Qualified request profiles: {guide['qualified_request_profiles']}",
        f"Requests needing inspection: {guide['unresolved_request_profiles']}",
        f"Application checks: {report['application_checks']['status']}",
        report["application_checks"]["next_action"],
    ]
    for issue in guide["issues"]:
        lines += [
            "",
            "Source: " + json.dumps(issue["source_file"], ensure_ascii=True),
            issue["summary"],
            issue["next_action"],
        ]
    for action in report["next_actions"]:
        lines += [
            "",
            action["summary"],
            "Argument vector: " + json.dumps(action["argv"], ensure_ascii=True),
        ]
        if action["requires"]:
            lines.append("Required decisions: " + ", ".join(action["requires"]))
    lines += [
        "",
        "Startup proposals: " + ", ".join(report["startup"]["proposal_statuses"] or ["none"]),
        "Tool interpreter versions are separate from the application's installed SDK pins.",
        "No collector contact, provider call, test execution or application edit occurred.",
        "Analysis is saved only in the application's private .cutback workspace.",
        "Use --json for bounded workflow/startup argument vectors and exact report identity.",
    ]
    return "\n".join(lines)
