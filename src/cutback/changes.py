"""Reviewable source changes and deterministic canary decisions; never deploys traffic."""

import ast
import difflib
import hashlib
import subprocess
from pathlib import Path

from pydantic import Field

from cutback.core.instruction_edits import LiteralInstructionEdit, qualify_instruction_edit
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
    return _request_patch(
        root,
        inventory,
        call_site_id,
        recommendation,
        expected_source_sha256=expected_source_sha256,
        proposal_only=proposal_only,
    )


def instruction_patch(
    root: Path,
    inventory: dict,
    call_site_id: str,
    recommendation: dict,
    *,
    expected_source_sha256: str,
    instruction_edit: LiteralInstructionEdit,
    max_instruction_bytes: int,
    proposal_only: bool = False,
) -> dict:
    """Prepare a separately qualified literal instruction diff; never apply it."""
    if type(max_instruction_bytes) is not int or not 1 <= max_instruction_bytes <= 16000:
        raise ValueError("INSTRUCTION_PATCH_REQUIRES_APPROVED_BYTE_BOUND")
    return _request_patch(
        root,
        inventory,
        call_site_id,
        recommendation,
        expected_source_sha256=expected_source_sha256,
        proposal_only=proposal_only,
        instruction_edit=instruction_edit,
        max_instruction_bytes=max_instruction_bytes,
    )


def _request_patch(
    root: Path,
    inventory: dict,
    call_site_id: str,
    recommendation: dict,
    *,
    expected_source_sha256: str,
    proposal_only: bool = False,
    instruction_edit: LiteralInstructionEdit | None = None,
    max_instruction_bytes: int = 16000,
) -> dict:
    root = root.resolve(strict=True)
    if recommendation.get("status") != "accepted_for_staging" and not (
        proposal_only and recommendation.get("status") in {"review_required", "proposed", "pending"}
    ):
        raise ValueError("RECOMMENDATION_REQUIRES_REVIEW")
    evidence = recommendation["evidence"]
    from cutback.core.evaluation_criteria import source_evaluator_eligible

    if not source_evaluator_eligible(evidence):
        raise ValueError("EVALUATOR_REAPPROVAL_REQUIRED")
    if not evidence.get("quality_gate_passed") or not evidence.get("cheaper_on_sample"):
        raise ValueError("CANDIDATE_DID_NOT_PASS_COST_AND_QUALITY_GATES")
    if evidence.get("lineage", {}).get("fresh_test_claim") is not True:
        raise ValueError("TEST_LINEAGE_REAPPROVAL_REQUIRED")
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
    if candidate.get("exact_cache") is not None or baseline.get("exact_cache") is not None:
        if instruction_edit is not None:
            raise ValueError("EXACT_CACHE_CANNOT_CHANGE_INSTRUCTIONS_IN_THE_SAME_PATCH")
        from cutback.cache_changes import prepare_patch

        return prepare_patch(
            root, current, site, profile, recommendation, proposal_only=proposal_only
        )
    if any(candidate.get(key) != baseline.get(key) for key in ("routes", "prompt_cache")):
        raise ValueError("REQUEST_VARIANT_REQUIRES_SEPARATE_PATCH")
    if candidate["provider"] != site["provider"] or baseline["provider"] != site["provider"]:
        raise ValueError("PROVIDER_CHANGE_REQUIRES_MANUAL_REVIEW")
    instruction_changed = candidate.get("instruction") != baseline.get(
        "instruction"
    ) or candidate.get("instruction_sha256") != baseline.get("instruction_sha256")
    if instruction_changed and instruction_edit is None:
        raise ValueError("PROMPT_VARIANT_REQUIRES_SEPARATE_TEMPLATE_PATCH")
    if instruction_edit is not None:
        if qualify_instruction_edit(root, site, profile) != instruction_edit:
            raise ValueError("INSTRUCTION_EDIT_CHANGED_SINCE_CAMPAIGN_APPROVAL")
        if (
            not isinstance(candidate.get("instruction"), str)
            or instruction_changed
            and len(candidate["instruction"].encode()) > max_instruction_bytes
        ):
            raise ValueError("INSTRUCTION_PATCH_OUTSIDE_APPROVED_BYTE_BOUND")

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
    if instruction_changed:
        assert instruction_edit is not None
        instruction_text = repr(candidate["instruction"])
        if instruction_edit.mode == "insert":
            instruction_text = ", " + instruction_edit.keyword + "=" + instruction_text
        edits.append((instruction_edit.start, instruction_edit.end, instruction_text.encode()))
        transformations.append("instruction")
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
    # A shared model constant can also feed an instruction, payload or option.
    # Recompile the proposed request through an in-memory source overlay: an
    # AST-only literal diff does not prove those evaluated semantics survived.
    try:
        proposed = prepare_profile(
            root,
            {**matching[0], "requested_model": candidate["model"]},
            workflow_id=binding.workflow_id,
            commit=commit,
            source_overrides={path.relative_to(root).as_posix(): changed},
        )
    except (ValueError, OSError, SyntaxError, TypeError, RecursionError):
        raise ValueError("PATCH_CHANGED_UNEVALUATED_REQUEST_CONTRACT") from None
    if (
        proposed.model != candidate["model"]
        or proposed.provider != candidate["provider"]
        or proposed.max_output_tokens != candidate.get("max_output_tokens")
        or fingerprint(proposed.request.model_dump(mode="json")) != contract_hash(candidate)
        or fingerprint(proposed.instruction)
        != (candidate.get("instruction_sha256") or fingerprint(candidate.get("instruction", "")))
    ):
        raise ValueError("PATCH_CHANGED_UNEVALUATED_REQUEST_CONTRACT")
    expected_dependencies = {
        **binding.dependencies,
        path.relative_to(root).as_posix(): hashlib.sha256(changed).hexdigest(),
    }
    if proposed.binding.dependencies != expected_dependencies:
        raise ValueError("PATCH_SOURCE_DEPENDENCIES_CHANGED_DURING_REVIEW")
    if instruction_edit:
        before_shape = _instruction_request_shape(
            source, site, binding.model_binding, instruction_edit, baseline
        )
        after_shape = _instruction_request_shape(
            after, site, binding.model_binding, instruction_edit, candidate
        )
        if before_shape != after_shape:
            raise ValueError("INSTRUCTION_PATCH_CHANGED_UNEVALUATED_SOURCE")
    relative = path.relative_to(root).as_posix()

    def diff(before, after):
        lines = difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="a/" + relative,
            tofile="b/" + relative,
        )
        return "".join(
            line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
            for line in lines
        )

    patch = diff(source, after)
    from cutback.evals.datasets import redact

    if redact(patch, []) != patch or redact(diff(after, source), []) != diff(after, source):
        raise ValueError("PATCH_CONTEXT_REQUIRES_MINIMAL_PRIVATE_REVIEW")
    from cutback.core.inventory import workflow_identity

    affected = {binding.workflow_id}
    unresolved = set()
    if len(current["call_sites"]) > 200:
        raise ValueError("AFFECTED_CONSUMER_ANALYSIS_BOUND_REQUIRES_SEPARATE_REVIEW")
    for other in current["call_sites"]:
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
                unresolved.add(identity)
    result = {
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
        "unresolved_consumer_workflows": sorted(unresolved),
        "scanner_warning_codes": sorted({w["code"] for w in current.get("warnings", [])}),
        "consumer_scope": "scanner_detected_source_only_not_runtime_or_external_consumers",
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
    if instruction_edit:
        result["instruction_edit"] = instruction_edit.model_dump(mode="json")
        result["verification"]["native_request_fields"] = (
            "exact_literal_model_instruction_and_output_bound"
        )
    return result


def _instruction_request_shape(source, site, model_binding, instruction_edit, expected):
    """Compare entire parsed modules after removing only the approved field values.

    No temporary source copy, application import, expression evaluation or file
    mutation is needed to prove that unrelated request/source fields stayed intact.
    """
    tree = ast.parse(source)
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "create"
    ]
    if instruction_edit.call_index >= len(calls):
        raise ValueError("INSTRUCTION_PATCH_REQUEST_NOT_UNIQUE")
    call = calls[instruction_edit.call_index]
    keyword = instruction_edit.keyword
    instructions = [k for k in call.keywords if k.arg == keyword]
    if (
        len(instructions) > 1
        or instructions
        and (
            not isinstance(instructions[0].value, ast.Constant)
            or instructions[0].value.value != expected.get("instruction")
        )
        or not instructions
        and expected.get("instruction")
    ):
        raise ValueError("INSTRUCTION_PATCH_REQUEST_VALUE_MISMATCH")
    call.keywords = [k for k in call.keywords if k.arg != keyword]
    models = [k.value for k in call.keywords if k.arg == "model"]
    if len(models) != 1:
        raise ValueError("INSTRUCTION_PATCH_MODEL_NOT_UNIQUE")
    if isinstance(models[0], ast.Constant):
        model = models[0]
    else:
        literals = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant)
            and (n.lineno, n.col_offset) == (model_binding["line"], model_binding["column"])
        ]
        if len(literals) != 1:
            raise ValueError("INSTRUCTION_PATCH_MODEL_BINDING_CHANGED")
        model = literals[0]
    if model.value != expected["model"]:
        raise ValueError("INSTRUCTION_PATCH_MODEL_VALUE_MISMATCH")
    model.value = "__CUTBACK_APPROVED_MODEL__"
    cap_key = "max_output_tokens" if site["sdk_surface"] == "responses.create" else "max_tokens"
    caps = [k.value for k in call.keywords if k.arg == cap_key]
    if len(caps) != 1:
        raise ValueError("INSTRUCTION_PATCH_OUTPUT_BOUND_NOT_UNIQUE")
    if isinstance(caps[0], ast.Constant):
        if type(caps[0].value) is not int or caps[0].value != expected["max_output_tokens"]:
            raise ValueError("INSTRUCTION_PATCH_OUTPUT_BOUND_VALUE_MISMATCH")
        caps[0].value = "__CUTBACK_APPROVED_OUTPUT_BOUND__"
    return ast.dump(tree, include_attributes=False)


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
