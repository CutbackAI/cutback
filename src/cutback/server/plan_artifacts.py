"""Prompt variants/rubrics are encrypted artifacts, never plaintext analytical records."""

import json
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from cutback.evals.datasets import digest
from cutback.evals.experiments import ExperimentPlan
from cutback.server.content_policy import policy_content


def public_config(value):
    if isinstance(value, list):
        return [public_config(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in {"instruction", "judge_rubric", "schema_definition", "allowed_labels"}:
            result[key] = "" if key == "instruction" else None
            result[key + "_sha256"] = digest(item)
        else:
            result[key] = public_config(item)
    return result


def freeze_plan(store, project, specification, policy, policy_id):
    raw = specification.model_dump(mode="json")
    _, blob = policy_content(raw, policy, preserve_semantics=True)
    ref = store.put(project, blob, policy.retention_days)
    return {
        "public_specification": public_config(raw),
        "plan_hash": digest(raw),
        "artifact_ref": ref,
        "expires_at": (datetime.now(UTC) + timedelta(days=policy.retention_days)).isoformat(),
        "policy_id": str(policy_id),
    }


def load_plan(store, project, metadata):
    if datetime.fromisoformat(metadata["expires_at"]) <= datetime.now(UTC):
        raise ValueError("PLAN_ARTIFACT_EXPIRED")
    raw = json.loads(store.get(project, metadata["artifact_ref"]))
    if digest(raw) != metadata["plan_hash"]:
        raise ValueError("PLAN_INTEGRITY_FAILURE")
    try:
        plan = ExperimentPlan.model_validate(raw)
    except ValidationError:
        raise ValueError("PLAN_SCHEMA_INVALID_REAPPROVAL_REQUIRED") from None
    if digest(plan.model_dump(mode="json")) != metadata["plan_hash"]:
        raise ValueError("PLAN_SCHEMA_CHANGED_REAPPROVAL_REQUIRED")
    return plan
