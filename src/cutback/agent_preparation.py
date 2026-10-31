"""Durable nonbillable setup identities; never retries approval or inference."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from cutback.core.preparation import PREPARATION_CONTRACT


def begin(directory, state, adapter):
    from cutback.agent import save_session

    if state.get("preparation_conflict"):
        raise ValueError("PREPARATION_IDENTITY_RECONCILIATION_REQUIRED")
    previous = state.get("preparation_intent")
    if previous is None:
        if state["phase"] == "PREPARING":
            raise ValueError("LEGACY_PREPARATION_REQUIRES_OPERATOR_RECONCILIATION")
        if state["phase"] == "PLAN_READY":
            return None
        previous = {
            "version": PREPARATION_CONTRACT,
            "adapter": adapter,
            "analysis_sha256": state["analysis_sha256"],
            "content_nonce": str(uuid4()),
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "requests": {name: str(uuid4()) for name in ("scan", "policy", "dataset", "plan")},
        }
        state["preparation_intent"] = previous
        save_session(directory, state)
    if (
        previous.get("version") != PREPARATION_CONTRACT
        or previous.get("adapter") != adapter
        or previous.get("analysis_sha256") != state["analysis_sha256"]
    ):
        raise ValueError("PREPARATION_INTENT_CHANGED_REANALYZE")
    try:
        values = previous["requests"]
        identifiers = [UUID(values[name]) for name in ("scan", "policy", "dataset", "plan")]
        expires = datetime.fromisoformat(previous["expires_at"])
        if UUID(previous["content_nonce"]).version != 4:
            raise ValueError
        if len(set(identifiers)) != 4 or any(value.version != 4 for value in identifiers):
            raise ValueError
        if expires.tzinfo is None:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValueError("PREPARATION_INTENT_INVALID") from None
    if expires <= datetime.now(UTC):
        raise ValueError("PREPARATION_RIGHTS_EXPIRED_NEW_REVIEW_REQUIRED")
    return previous


def expiry(intent):
    return (
        datetime.fromisoformat(intent["expires_at"])
        if intent
        else datetime.now(UTC) + timedelta(days=1)
    )


def require_protocol(control, *, native_fixture=False):
    ready = control("GET", "/v1/health/ready")
    if not isinstance(ready, dict) or ready.get("preparation_contract") != PREPARATION_CONTRACT:
        raise ValueError("COLLECTOR_DURABLE_PREPARATION_UPGRADE_REQUIRED")
    if native_fixture:
        from cutback.core.native_fixture import NATIVE_FIXTURE_CONTRACT

        if ready.get("native_fixture_contract") != NATIVE_FIXTURE_CONTRACT:
            raise ValueError("COLLECTOR_NATIVE_FIXTURE_UPGRADE_REQUIRED")


def bind(original, intent, dataset, policy):
    """Bind current transformed examples and rights without journaling their content."""
    if intent is None:
        return original  # Preserve completed legacy preparation identities.
    from cutback.core.request_profiles import fingerprint

    return fingerprint(
        {
            "version": PREPARATION_CONTRACT,
            "original": original,
            "dataset_import_sha256": fingerprint(
                dataset.model_dump(mode="json", exclude={"policy_id"})
            ),
            "policy_sha256": fingerprint(policy.model_dump(mode="json")),
            "requests": intent["requests"],
            "content_nonce": intent["content_nonce"],
        }
    )


def conflict(directory, state, resource, expected, observed=None, *, reason="identity_mismatch"):
    from cutback.agent import save_session

    safe_observed = None
    if type(observed) is str:
        try:
            value = UUID(observed)
            if value.version == 4:
                safe_observed = str(value)
        except ValueError:
            pass
    state["preparation_conflict"] = {
        "resource": resource,
        "expected_request_id": expected,
        "observed_request_id": safe_observed,
        "reason": reason,
    }
    save_session(directory, state)


def create(control, base, resource, body, intent, *, state, directory, response_key="id"):
    name = {"scans": "scan", "capture-policies": "policy", "datasets": "dataset"}[resource]
    identifier = intent["requests"][name]
    try:
        result = control("POST", base + "/" + resource + "?request_id=" + identifier, body)
    except ValueError as exc:
        if str(exc) in {"PREPARATION_IDENTITY_CONFLICT", "PREPARATION_CONTENT_NO_LONGER_AVAILABLE"}:
            conflict(directory, state, resource, identifier, reason=str(exc))
        raise
    observed = result.get(response_key) if isinstance(result, dict) else None
    if observed != identifier:
        conflict(directory, state, resource, identifier, observed)
        raise ValueError("COLLECTOR_DURABLE_PREPARATION_IDENTITY_MISMATCH")
    return result


def plan(control, base, dataset_id, specification, intent, *, state, directory):
    result = control(
        "POST",
        base + "/experiments/plans",
        {
            "request_id": intent["requests"]["plan"],
            "dataset_id": dataset_id,
            "specification": specification.model_dump(mode="json"),
        },
    )
    observed = result.get("id") if isinstance(result, dict) else None
    if observed != intent["requests"]["plan"]:
        conflict(directory, state, "experiments/plans", intent["requests"]["plan"], observed)
        raise ValueError("COLLECTOR_DURABLE_PREPARATION_IDENTITY_MISMATCH")
    return result


def resume(root, control):
    from cutback.agent import load_session, prepare, public_session, workspace
    from cutback.core.workspace import workspace_lock

    root, directory = workspace(root)
    with workspace_lock(directory):
        state = load_session(root)
        if state.get("endpoint") != control.endpoint:
            raise ValueError("COLLECTOR_DESTINATION_MISMATCH")
        if state.get("preparation_conflict"):
            raise ValueError("PREPARATION_IDENTITY_RECONCILIATION_REQUIRED")
        if state["phase"] == "PLAN_READY" and state.get("preparation_intent"):
            return public_session(state)
        if state["phase"] != "PREPARING" or not state.get("preparation_intent"):
            raise ValueError("NO_RESUMABLE_NONBILLABLE_PREPARATION")
        options = state.get("preparation_command")
        if not isinstance(options, dict) or not options:
            raise ValueError("LEGACY_PREPARATION_REQUIRES_ORIGINAL_OPTIONS")
        allowed = {
            "files",
            "rights_basis",
            "rights_ref",
            "max_usd",
            "max_requests",
            "workflow",
            "minimum",
            "independent_labels",
            "representative",
            "retain_case_outputs",
            "mapping",
            "search",
            "for_campaign",
            "campaign_generator_provider",
            "criteria_generator_provider",
            "min_test_samples",
            "joint_context",
            "assumed_usd_per_cpu_hour",
            "learned_routing",
            "fixture_transport",
        }
        if set(options) - allowed:
            raise ValueError("PREPARATION_OPTIONS_INVALID")
        project = state["project_id"]
    return prepare(root, control, project, **options)
