"""Prompt variants/rubrics are encrypted artifacts, never plaintext analytical records."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.exceptions import InvalidTag
from pydantic import ValidationError

from cutback.evals.datasets import digest
from cutback.evals.experiments import ExperimentPlan
from cutback.server.content_policy import policy_content


def target_evaluation_clause():
    """Generation receipts have no test split and are never target-quality runs."""
    from sqlalchemy import and_

    from cutback.server.db import ExperimentPlanRecord

    operation = ExperimentPlanRecord.specification["public_specification"]["operation"].as_string()
    return and_(
        operation.is_distinct_from("candidate-generation-v1"),
        operation.is_distinct_from("criteria-generation-v1"),
        operation.is_distinct_from("criteria-generation-v2"),
        operation.is_distinct_from("scenario-generation-v1"),
        operation.is_distinct_from("source-policy-generation-v1"),
        ExperimentPlanRecord.specification["public_specification"]["routing_execution"]["phase"]
        .as_string()
        .is_distinct_from("training"),
        ExperimentPlanRecord.specification["public_specification"]["source_policy_execution"][
            "phase"
        ]
        .as_string()
        .is_distinct_from("training"),
    )


def public_config(value, *, redact_content_hashes=False, redact_criteria=False):
    if isinstance(value, list):
        return [
            public_config(
                item, redact_content_hashes=redact_content_hashes, redact_criteria=redact_criteria
            )
            for item in value
        ]
    if not isinstance(value, dict):
        return value
    redact_content_hashes = redact_content_hashes or bool(
        value.get("prompt_bindings") or value.get("content_nonce")
    )
    redact_criteria = redact_criteria or bool(value.get("criteria_binding"))
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key == "source_policy_execution" and item is not None:
            from cutback.core.policy_execution import (
                PublicPolicyExecution,
                SourcePolicyExecutionSpec,
            )

            result[key] = (
                SourcePolicyExecutionSpec.model_validate(item).public_summary()
                if "reference" in item or "candidates" in item
                else PublicPolicyExecution.model_validate(item).model_dump(
                    mode="json", exclude_unset=True
                )
            )
            continue
        if key == "output_field" and redact_criteria:
            result[key] = None
            continue
        if key == "retrieval_execution":
            if item is None:
                result[key] = None
            else:
                from cutback.evals.retrieval import RetrievalExecutionSpec
                from cutback.evals.retrieval_joint import (
                    JOINT_RETRIEVAL_CONTRACT,
                    JointModelRetrievalExecutionSpec,
                )

                result[key] = (
                    JointModelRetrievalExecutionSpec.model_validate(item).public_summary()
                    if item.get("version") == JOINT_RETRIEVAL_CONTRACT
                    else RetrievalExecutionSpec.model_validate(item).public_summary()
                )
            continue
        if key in {"prompt_variables", "sampling_options", "content_nonce"} or (
            key == "request_contract"
            and isinstance(item, dict)
            and item.get("version") == "native-prompt-v1"
        ):
            result[key] = None
            continue
        if key in {
            "instruction",
            "judge_rubric",
            "schema_definition",
            "allowed_labels",
            "request_contract",
        }:
            result[key] = "" if key == "instruction" else None
            if not redact_content_hashes:
                result[key + "_sha256"] = digest(item)
        else:
            result[key] = public_config(
                item, redact_content_hashes=redact_content_hashes, redact_criteria=redact_criteria
            )
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
    try:
        raw = json.loads(store.get(project, metadata["artifact_ref"]))
    except (OSError, ValueError, InvalidTag):
        raise ValueError("PLAN_ARTIFACT_UNAVAILABLE") from None
    if digest(raw) != metadata["plan_hash"]:
        raise ValueError("PLAN_INTEGRITY_FAILURE")
    try:
        plan = ExperimentPlan.model_validate(raw)
    except ValidationError:
        raise ValueError("PLAN_SCHEMA_INVALID_REAPPROVAL_REQUIRED") from None
    if digest(plan.model_dump(mode="json")) != metadata["plan_hash"]:
        raise ValueError("PLAN_SCHEMA_CHANGED_REAPPROVAL_REQUIRED")
    return plan
