"""Reviewable source changes and deterministic canary decisions; never deploys traffic."""

import ast
import difflib
import hashlib
import subprocess
from pathlib import Path

from pydantic import Field

from cutback.core.models import StrictModel


def model_patch(
    root: Path,
    inventory: dict,
    call_site_id: str,
    recommendation: dict,
    *,
    expected_source_sha256: str,
) -> dict:
    root = root.resolve(strict=True)
    if recommendation.get("status") != "accepted_for_staging":
        raise ValueError("RECOMMENDATION_REQUIRES_REVIEW")
    evidence = recommendation["evidence"]
    if evidence.get("evaluator_version") != "deterministic-v3":
        raise ValueError("EVALUATOR_REAPPROVAL_REQUIRED")
    if not evidence.get("quality_gate_passed") or not evidence.get("cheaper_on_sample"):
        raise ValueError("CANDIDATE_DID_NOT_PASS_COST_AND_QUALITY_GATES")
    sites = [site for site in inventory["call_sites"] if site["call_site_id"] == call_site_id]
    if len(sites) != 1:
        raise ValueError("CALL_SITE_NOT_UNIQUE")
    site = sites[0]
    path = root / site["source_file"]
    if (
        path.is_symlink()
        or not path.resolve().is_relative_to(root)
        or path.stat().st_size > 1048576
    ):
        raise ValueError("UNSAFE_SOURCE_PATH")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_source_sha256:
        raise ValueError("SOURCE_CHANGED_SINCE_REVIEW")
    commit = inventory["project"].get("commit")
    if commit:
        actual = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if actual.returncode or actual.stdout.strip() != commit:
            raise ValueError("REPOSITORY_COMMIT_CHANGED")
    from cutback.scanner import scan

    current = scan(root)
    matching = [s for s in current["call_sites"] if s["call_site_id"] == call_site_id]
    if len(matching) != 1 or matching[0]["source_file"] != site["source_file"]:
        raise ValueError("CALL_SITE_CHANGED")
    baseline = evidence["rollback"]["baseline"]
    candidate = evidence["candidate"]
    if candidate["provider"] != site["provider"] or baseline["provider"] != site["provider"]:
        raise ValueError("PROVIDER_CHANGE_REQUIRES_MANUAL_REVIEW")
    if candidate.get("instruction") != baseline.get("instruction"):
        raise ValueError("PROMPT_VARIANT_REQUIRES_SEPARATE_TEMPLATE_PATCH")
    if candidate.get("instruction_sha256") != baseline.get("instruction_sha256"):
        raise ValueError("PROMPT_VARIANT_REQUIRES_SEPARATE_TEMPLATE_PATCH")
    source = raw.decode("utf-8")
    calls = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and node.lineno == matching[0]["line"]
    ]
    literals = [
        keyword.value
        for call in calls
        for keyword in call.keywords
        if keyword.arg == "model"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value == baseline["model"]
    ]
    if len(literals) != 1:
        raise ValueError("DYNAMIC_OR_AMBIGUOUS_MODEL_REQUIRES_MANUAL_PATCH")
    literal = literals[0]
    if literal.end_lineno is None or literal.end_col_offset is None:
        raise ValueError("SOURCE_RANGE_UNAVAILABLE")
    lines = source.encode().splitlines(keepends=True)
    start = sum(map(len, lines[: literal.lineno - 1])) + literal.col_offset
    end = sum(map(len, lines[: literal.end_lineno - 1])) + literal.end_col_offset
    after = (raw[:start] + repr(candidate["model"]).encode() + raw[end:]).decode()
    ast.parse(after)
    relative = path.relative_to(root).as_posix()

    def diff(before, after):
        return "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile="a/" + relative,
                tofile="b/" + relative,
            )
        )

    patch = diff(source, after)
    return {
        "status": "DRY_RUN_ONLY",
        "diff": patch,
        "rollback_diff": diff(after, source),
        "diff_sha256": hashlib.sha256(patch.encode()).hexdigest(),
        "source_sha256": expected_source_sha256,
        "source_file": relative,
        "call_site_id": call_site_id,
        "source_commit": commit,
        "recommendation_id": recommendation["id"],
        "run_id": recommendation["run_id"],
        "dataset_hash": evidence["dataset_hash"],
        "plan_hash": evidence["plan_hash"],
        "quality_scope": evidence["status"],
        "production_deployed": False,
        "staging_steps": [
            "review and apply the diff in an isolated branch",
            "run application tests and provider compatibility smoke tests",
            "approve staging deployment and an explicit traffic budget",
            "record baseline/candidate windows before a canary decision",
        ],
        "canary": CanaryPolicy().model_dump(),
    }


class CanaryPolicy(StrictModel):
    min_observations: int = Field(default=100, ge=1, le=1000000)
    max_error_rate: float = Field(default=0.01, ge=0, le=1)
    max_schema_failure_rate: float = Field(default=0, ge=0, le=1)
    max_latency_ratio: float = Field(default=1.2, ge=0.1, le=10)
    max_cost_ratio: float = Field(default=1, ge=0, le=10)
    minimum_business_quality: float | None = Field(default=None, ge=0, le=1)


def canary_decision(policy: CanaryPolicy, baseline: dict, candidate: dict) -> dict:
    reasons = []
    if candidate.get("observations", 0) < policy.min_observations:
        return {
            "state": "INSUFFICIENT_EVIDENCE",
            "action": "continue_only_with_approved_budget",
            "traffic_changed": False,
        }
    pairs = (
        ("error_rate", policy.max_error_rate),
        ("schema_failure_rate", policy.max_schema_failure_rate),
    )
    for name, threshold in pairs:
        if candidate.get(name) is None:
            reasons.append(name + "_unknown")
        elif candidate[name] > threshold:
            reasons.append(name + "_breached")
    for name, ratio in (
        ("latency_p95_ms", policy.max_latency_ratio),
        ("cost_per_request_usd", policy.max_cost_ratio),
    ):
        if baseline.get(name) is None or candidate.get(name) is None:
            reasons.append(name + "_unknown")
        elif candidate[name] > baseline[name] * ratio:
            reasons.append(name + "_breached")
    quality = candidate.get("business_quality")
    if policy.minimum_business_quality is not None and (
        quality is None or quality < policy.minimum_business_quality
    ):
        reasons.append("business_quality_unknown_or_breached")
    return {
        "state": "ROLLBACK_RECOMMENDED" if reasons else "GUARDRAILS_PASSED",
        "reasons": reasons,
        "semantic_quality": "NOT VERIFIED"
        if policy.minimum_business_quality is None
        else "observed_business_metric_only",
        "action": "operator_review_required",
        "traffic_changed": False,
    }
