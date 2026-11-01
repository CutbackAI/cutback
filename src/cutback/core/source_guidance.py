"""Fixed, parse-only explanations; exceptions and application values never become copy."""

import json

_GROUPS = [
    (
        {"CUSTOM_PROVIDER_DESTINATION_REQUIRES_REVIEW"},
        "The source uses client or destination settings outside the replay contract.",
        "Metadata observation may continue with a supported SDK. Preserve the deployment settings; "
        "qualify their explicit client/destination adapter before source replay.",
    ),
    (
        {"REQUEST_PAYLOAD_AND_EXPLICIT_OUTPUT_BOUND_REQUIRED", "OUTPUT_BOUND_NOT_QUALIFIED"},
        "The request needs an explicit supported payload and output-token ceiling.",
        "Inspect the actual call and its provider token-limit argument. Make the bound explicit "
        "only if it preserves application behavior, then rerun offline discovery "
        "and application tests.",
    ),
    (
        {"MODEL_REFERENCE_UNRESOLVED", "MODEL_EDIT_REQUIRES_REVIEW"},
        "The scanner cannot freeze a unique supported model reference for this call.",
        "Inspect the model's configuration and routing. A supported literal or resolvable constant "
        "can be reanalyzed; dynamic routing needs its own qualified source adapter.",
    ),
    (
        {"EXPANDED_REQUEST_ARGUMENTS_REQUIRE_REVIEW", "DUPLICATE_REQUEST_ARGUMENTS_REQUIRE_REVIEW"},
        "Expanded or ambiguous keyword arguments prevent an exact request contract.",
        "Inspect the effective request options. Expose explicit supported arguments only when "
        "behavior is unchanged; otherwise qualify an adapter for the existing wrapper.",
    ),
    (
        {"UNQUALIFIED_REQUEST_OPTIONS"},
        "Some request options are outside the inspected replay surface.",
        "Keep options required by the application. Check docs/providers.md and qualify "
        "their native "
        "response, privacy and failure behavior before widening the replay adapter.",
    ),
    (
        {"REQUEST_STORAGE_POLICY_REQUIRES_REVIEW"},
        "The source storage behavior does not match the replay privacy contract.",
        "Review the application's storage requirement. A policy change needs its own application "
        "review; source replay must preserve the approved data-retention behavior.",
    ),
    (
        {
            "DYNAMIC_INSTRUCTION_REQUIRES_REVIEW",
            "TRANSFORMED_REQUEST_PARAMETER_REQUIRES_REVIEW",
            "DYNAMIC_REQUEST_VALUE_REQUIRES_REVIEW",
            "DYNAMIC_LOCAL_REQUEST_VALUE_REQUIRES_REVIEW",
            "CONDITIONAL_REQUEST_VALUE_REQUIRES_REVIEW",
            "MUTATED_REQUEST_VALUE_REQUIRES_REVIEW",
            "DYNAMIC_REQUEST_SCOPE_REQUIRES_REVIEW",
            "CLOSURE_REQUEST_SCOPE_REQUIRES_REVIEW",
            "DYNAMIC_REQUEST_TYPE_SCOPE_REQUIRES_REVIEW",
            "NONFUNCTION_REQUEST_SCOPE_REQUIRES_REVIEW",
            "DYNAMIC_REQUEST_IMPORT_ALIAS_REQUIRES_REVIEW",
            "DYNAMIC_MODULE_ALIAS_REQUIRES_REVIEW",
        },
        "Runtime construction or preprocessing prevents a faithful static replay contract.",
        "Preserve the preprocessing. Review rights-cleared post-transform fixtures "
        "and the wrapper's "
        "inputs, outputs and effects; qualify a bounded adapter before automatic source changes.",
    ),
    (
        {"PROFILE_RESOLUTION_LIMIT", "REQUEST_CONTRACT_TOO_LARGE"},
        "The bounded source inspection could not finish this request contract.",
        "Select one detected workflow with cutback optimize --workflow WORKFLOW_ID. Inspect the "
        "remaining bounded scope; increasing a limit does not establish replay safety.",
    ),
]
_LOOKUP = {code: (summary, action) for codes, summary, action in _GROUPS for code in codes}
_FALLBACK = (
    "This detected call needs a separately inspected request adapter.",
    "Inspect the call's model, payload, options, client and effects against docs/providers.md. "
    "Keep metadata-only observation separate from replay and source-change qualification.",
)


def request_guidance(error):
    """Only recognize fixed owned codes; never expose arbitrary exception details."""
    code = str(error) if type(error) is ValueError else ""
    if code.startswith("UNQUALIFIED_REQUEST_OPTIONS:"):
        code = "UNQUALIFIED_REQUEST_OPTIONS"
    if code not in _LOOKUP:
        code = "REQUEST_REQUIRES_INSPECTED_ADAPTER_OR_EXPLICIT_BOUND"
    summary, action = _LOOKUP.get(code, _FALLBACK)
    return {
        "diagnostic_code": code,
        "summary": summary,
        "next_action": action,
        "replay_qualified": False,
        "runtime_observation_coverage": "requires_separate_supported_sdk_qualification",
    }


def explain_source(state):
    """Summarize the saved bounded discovery without reading source or making calls."""
    profiles = state.get("profiles", [])
    unresolved = state.get("unresolved", [])
    options = state.get("preparation_options", [])
    policies = state.get("policy_producers", [])
    if policies:
        note = (
            "A fixed source-policy consumer is qualified. Prepare its whole-budget program "
            "using permitted frozen data and a measured task-cost snapshot. Preparation can "
            "discover a unique compatible pair; inspect source-policy discover when evidence "
            "is missing or versions are ambiguous. Discovery grants no execution authority."
        )
    elif options:
        note = "Some workflows have inspected adapters. Unresolved calls stay outside them."
    elif unresolved:
        note = "Detected calls still need request-contract inspection before automatic replay."
    else:
        note = (
            "No qualified static request or source adapter was found. Check the application root, "
            "workflow selection, ignore rules and supported provider/framework contracts."
        )
    issues = []
    for issue in unresolved[:20]:
        guide = request_guidance(ValueError(issue.get("diagnostic_code", "")))
        issues.append(
            {
                "source_file": issue.get("source_file"),
                "workflow_id": issue.get("workflow_id"),
                **guide,
            }
        )
    return {
        "version": "cutback.source-guidance.v1",
        "phase": state["phase"],
        "qualified_request_profiles": len(profiles),
        "qualified_preparation_choices": len(options),
        "qualified_source_policy_producers": len(policies),
        "unresolved_request_profiles": len(unresolved),
        "issues": issues,
        "omitted_issues": max(0, len(unresolved) - len(issues)),
        "discovery_note": note,
        "profile_analysis": state.get("profile_analysis", {}),
        "next_action": {
            key: value
            for key, value in state.get("next_action", {}).items()
            if key in {"code", "tool", "requires", "effects", "provider_calls", "replay_qualified"}
        },
        "provider_calls": 0,
        "source_changed": None,
        "source_edit_performed": False,
        "source_freshness": "not_rechecked_saved_snapshot_only",
        "coverage": "detected_bounded_static_source_not_runtime_or_exhaustive_coverage",
    }


def render_source_guide(report):
    lines = [
        f"Cutback workflow: {report['phase']}",
        f"Qualified request profiles: {report['qualified_request_profiles']}",
        f"Qualified preparation choices: {report['qualified_preparation_choices']}",
        f"Requests needing inspection: {report['unresolved_request_profiles']}",
    ]
    lines += ["", report["discovery_note"]]
    omitted = report["profile_analysis"].get("omitted_call_sites", 0)
    if type(omitted) is int and omitted > 0:
        lines.append(f"Detected call sites outside this bounded inspection: {omitted}.")
    action = report["next_action"]
    if action:
        lines += [
            "",
            "Next action: " + str(action.get("code", "INSPECT_SAVED_WORKFLOW")),
            "Tool: " + str(action.get("tool", "cutback workflow status")),
        ]
    for index, issue in enumerate(report["issues"], 1):
        # Quote file names so newline/control characters cannot forge guide sections.
        lines += [
            "",
            f"{index}. {json.dumps(issue['source_file'], ensure_ascii=True)}",
            issue["summary"],
            issue["next_action"],
        ]
    if report["omitted_issues"]:
        lines.append(f"{report['omitted_issues']} additional issues remain in workflow status.")
    lines += [
        "",
        "Saved analysis only; rerun cutback optimize to inspect current source.",
        "No provider call or source edit was made.",
        "Static discovery does not establish complete runtime coverage or customer savings.",
    ]
    return "\n".join(lines)
