"""Offline adapter selection; eligibility is not a claim of measured savings."""

SELECTION_VERSION = "qualified-source-preparation-v1"


def preparation_options(state):
    """Keep qualified wrappers intact instead of replaying an inner native call."""
    choices: dict[str, set[str]] = {}
    for field, mode in (
        ("context_producers", "joint_model_context"),
        ("routing_producers", "source_routing"),
    ):
        for value in state.get(field, []):
            workflow = value["workflow_id"]
            modes = choices.setdefault(workflow, set())
            modes.add(mode)
    for profile in state.get("profiles", []):
        workflow = profile["binding"]["workflow_id"]
        if workflow not in choices:
            choices[workflow] = {"native_search"}
    return [
        {
            "workflow_id": workflow,
            "modes": sorted(modes),
            "status": "qualified_source_adapter"
            if len(modes) == 1
            else "ambiguous_source_adapters",
        }
        for workflow, modes in sorted(choices.items())
    ]


def select_preparation(state, workflow=None):
    choices = [
        value
        for value in preparation_options(state)
        if workflow is None or value["workflow_id"] == workflow
    ]
    if len(choices) != 1:
        raise ValueError("AUTOMATIC_PREPARATION_REQUIRES_ONE_QUALIFIED_WORKFLOW")
    selected = choices[0]
    if len(selected["modes"]) != 1:
        raise ValueError("AUTOMATIC_PREPARATION_REQUIRES_UNAMBIGUOUS_SOURCE_ADAPTER")
    return {
        "version": SELECTION_VERSION,
        "workflow_id": selected["workflow_id"],
        "mode": selected["modes"][0],
        "analysis_sha256": state["analysis_sha256"],
        "basis": "qualified_source_contract_not_measured_strategy_economics",
        "expected_savings_usd": None,
        "provider_calls": 0,
        "other_engines": {
            "exact_cache": "requires_repeat_freshness_and_private_response_evidence",
            "training": "requires_training_rights_compute_and_serving_evidence",
            "semantic_cache": "requires_separate_namespace_and_quality_qualification",
        },
    }


def freeze_preparation(root, workflow=None):
    """Save the nonbillable intent before any collector request; resume the same choice."""
    from cutback.agent import load_session, save_session, workspace
    from cutback.core.workspace import workspace_lock

    root, directory = workspace(root)
    with workspace_lock(directory):
        state = load_session(root)
        if state["phase"] not in {"ANALYZED", "SOURCE_CHANGED", "PLAN_READY", "PREPARING"}:
            raise ValueError("WORKFLOW_PREPARATION_ALREADY_STARTED_INSPECT_STATUS")
        selected = select_preparation(state, workflow)
        previous = state.get("automatic_preparation")
        if state["phase"] == "PREPARING" and previous is None:
            raise ValueError("AUTOMATIC_PREPARATION_REQUIRES_ORIGINAL_ENGINE")
        if previous is not None and previous != selected:
            raise ValueError("AUTOMATIC_PREPARATION_INTENT_CHANGED_REANALYZE")
        if previous is None:
            state["automatic_preparation"] = selected
            save_session(directory, state)
        return selected
