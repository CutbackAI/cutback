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
    proposal_only: bool = False,
) -> dict:
    root = root.resolve(strict=True)
    if recommendation.get("status") != "accepted_for_staging" and not (
        proposal_only and recommendation.get("status") in {"review_required", "proposed", "pending"}
    ):
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
    from cutback.core.request_profiles import SourceBinding, fingerprint, prepare_profile

    if not evidence.get("source_binding"):
        raise ValueError("SOURCE_REQUEST_BINDING_REQUIRED")
    binding = SourceBinding.model_validate(evidence["source_binding"])
    edit_path = root / binding.model_binding["file"]
    if (
        edit_path.is_symlink()
        or not edit_path.resolve().is_relative_to(root)
        or any(parent.is_symlink() for parent in edit_path.parents if parent.is_relative_to(root))
    ):
        raise ValueError("UNSAFE_SOURCE_PATH")
    if (
        hashlib.sha256((root / binding.model_binding["file"]).read_bytes()).hexdigest()
        != expected_source_sha256
    ):
        raise ValueError("SOURCE_CHANGED_SINCE_REVIEW")
    profile = prepare_profile(root, matching[0], workflow_id=binding.workflow_id, commit=commit)
    if profile.binding != binding:
        raise ValueError("SOURCE_REQUEST_PROFILE_CHANGED")
    baseline = evidence["rollback"]["baseline"]
    candidate = evidence["candidate"]
    if any(candidate.get(key) != baseline.get(key) for key in ("routes", "prompt_cache")):
        raise ValueError("REQUEST_VARIANT_REQUIRES_SEPARATE_PATCH")
    if candidate["provider"] != site["provider"] or baseline["provider"] != site["provider"]:
        raise ValueError("PROVIDER_CHANGE_REQUIRES_MANUAL_REVIEW")
    if candidate.get("instruction") != baseline.get("instruction"):
        raise ValueError("PROMPT_VARIANT_REQUIRES_SEPARATE_TEMPLATE_PATCH")
    if candidate.get("instruction_sha256") != baseline.get("instruction_sha256"):
        raise ValueError("PROMPT_VARIANT_REQUIRES_SEPARATE_TEMPLATE_PATCH")

    def contract_hash(model):
        return model.get("request_contract_sha256") or fingerprint(model.get("request_contract"))

    if contract_hash(candidate) != contract_hash(baseline):
        raise ValueError("REQUEST_VARIANT_REQUIRES_SEPARATE_PATCH")
    if (
        contract_hash(baseline) != fingerprint(profile.request.model_dump(mode="json"))
        or (baseline.get("instruction_sha256") or fingerprint(baseline.get("instruction", "")))
        != fingerprint(profile.instruction)
        or baseline.get("max_output_tokens") != profile.max_output_tokens
    ):
        raise ValueError("EVALUATED_REQUEST_DOES_NOT_MATCH_SOURCE")
    edit = binding.model_binding
    path = root / edit["file"]
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_source_sha256:
        raise ValueError("SOURCE_CHANGED_SINCE_REVIEW")
    source = raw.decode("utf-8")
    literals = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant)
        and node.value == baseline["model"]
        and node.lineno == edit["line"]
        and node.col_offset == edit["column"]
    ]
    if len(literals) != 1:
        raise ValueError("DYNAMIC_OR_AMBIGUOUS_MODEL_REQUIRES_MANUAL_PATCH")
    literal = literals[0]
    if literal.end_lineno is None or literal.end_col_offset is None:
        raise ValueError("SOURCE_RANGE_UNAVAILABLE")
    lines = source.encode().splitlines(keepends=True)
    start = sum(map(len, lines[: literal.lineno - 1])) + literal.col_offset
    end = sum(map(len, lines[: literal.end_lineno - 1])) + literal.end_col_offset
    edits = (
        [(start, end, repr(candidate["model"]).encode())]
        if candidate["model"] != baseline["model"]
        else []
    )
    transformations = ["model"] if edits else []
    if candidate.get("max_output_tokens") != baseline.get("max_output_tokens"):
        cap = candidate.get("max_output_tokens")
        if type(cap) is not int or not 16 <= cap < profile.max_output_tokens:
            raise ValueError("OUTPUT_CAP_PATCH_REQUIRES_EVALUATED_REDUCTION")
        # A separate imported cap/config needs a multi-file patch contract. Never guess
        # another assignment from equal literal values elsewhere in the application.
        if site["source_file"] != edit["file"]:
            raise ValueError("OUTPUT_CAP_REQUIRES_SAME_FILE_LITERAL_PATCH")
        key = (
            "max_output_tokens"
            if profile.request.sdk_surface == "responses.create"
            else "max_tokens"
        )
        calls = [
            n
            for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.Call)
            and n.lineno == site["line"]
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "create"
        ]
        caps = [k.value for call in calls for k in call.keywords if k.arg == key]
        if len(calls) != 1 or len(caps) != 1 or not isinstance(caps[0], ast.Constant):
            raise ValueError("OUTPUT_CAP_REQUIRES_SAME_FILE_LITERAL_PATCH")
        node = caps[0]
        if type(node.value) is not int or node.value != profile.max_output_tokens:
            raise ValueError("EVALUATED_OUTPUT_CAP_DOES_NOT_MATCH_SOURCE")
        if node.end_lineno is None or node.end_col_offset is None:
            raise ValueError("SOURCE_RANGE_UNAVAILABLE")
        edits.append(
            (
                sum(map(len, lines[: node.lineno - 1])) + node.col_offset,
                sum(map(len, lines[: node.end_lineno - 1])) + node.end_col_offset,
                str(cap).encode(),
            )
        )
        transformations.append("output_cap")
    if not edits:
        raise ValueError("NO_EVALUATED_SOURCE_CHANGE")
    changed = raw
    for begin, finish, replacement in sorted(edits, reverse=True):
        changed = changed[:begin] + replacement + changed[finish:]
    after = changed.decode()
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
    from cutback.evals.datasets import redact

    if redact(patch, []) != patch or redact(diff(after, source), []) != diff(after, source):
        raise ValueError("PATCH_CONTEXT_REQUIRES_MINIMAL_PRIVATE_REVIEW")
    from cutback.core.inventory import workflow_identity

    affected = {binding.workflow_id}
    for other in current["call_sites"][:200]:
        entries = other["entry_points"] or [
            {"file": other["source_file"], "symbol": other["source_symbol"]}
        ]
        for entry in entries:
            identity, _ = workflow_identity(entry["file"], entry["symbol"])
            try:
                other_profile = prepare_profile(root, other, workflow_id=identity, commit=commit)
                if other_profile.binding.model_binding == edit:
                    affected.add(identity)
            except (ValueError, OSError, SyntaxError, TypeError, RecursionError):
                continue
    return {
        "status": "DRY_RUN_ONLY",
        "review_status": "human_review_required" if proposal_only else "accepted_for_staging",
        "diff": patch,
        "rollback_diff": diff(after, source),
        "diff_sha256": hashlib.sha256(patch.encode()).hexdigest(),
        "source_sha256": expected_source_sha256,
        "source_file": relative,
        "call_source_file": site["source_file"],
        "source_binding": binding.model_dump(mode="json"),
        "affected_workflows": sorted(affected),
        "unvalidated_workflows": sorted(affected - {binding.workflow_id}),
        "call_site_id": call_site_id,
        "source_commit": commit,
        "recommendation_id": recommendation["id"],
        "run_id": recommendation["run_id"],
        "dataset_hash": evidence["dataset_hash"],
        "plan_hash": evidence["plan_hash"],
        "quality_scope": evidence["status"],
        "transformations": transformations,
        "production_deployed": False,
        "verification": {
            "kind": "source_integrity_and_syntax_not_business_quality",
            "before_sha256": hashlib.sha256(raw).hexdigest(),
            "after_sha256": hashlib.sha256(after.encode()).hexdigest(),
            "application_tests": "agent_must_inspect_and_run_authorized_application_checks",
        },
        "staging_steps": [
            "review and apply the diff in an isolated branch",
            "run application tests and provider compatibility smoke tests",
            "qualify every affected workflow; this experiment covers only its bound workflow",
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
