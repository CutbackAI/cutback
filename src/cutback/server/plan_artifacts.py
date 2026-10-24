"""Prompt variants/rubrics are encrypted artifacts, never plaintext analytical records."""

import json
from datetime import UTC, datetime, timedelta

from cutback.evals.datasets import digest
from cutback.evals.experiments import ExperimentPlan


def public_config(value):
    if isinstance(value, list):
        return [public_config(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in {"instruction", "judge_rubric"}:
            result[key] = "" if key == "instruction" else None
            result[key + "_sha256"] = digest(item)
        else:
            result[key] = public_config(item)
    return result


def freeze_plan(store, project, specification, policy, policy_id):
    raw = specification.model_dump(mode="json")
    blob = json.dumps(raw).encode()
    if len(blob) > policy.max_payload_bytes:
        raise ValueError("PLAN_EXCEEDS_APPROVED_ARTIFACT_BYTES")
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
    return ExperimentPlan.model_validate(raw)
