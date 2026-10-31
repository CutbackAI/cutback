"""An opt-in exact-cache source adapter; parse and prepare a reversible diff only."""

import ast
import copy
import difflib
import hashlib

from cutback.core.exact_cache import ExactCacheExecution
from cutback.core.inventory import workflow_identity
from cutback.core.request_profiles import fingerprint
from cutback.evals.datasets import redact

HELPER_NAME = "_cutback_cached_responses"
HELPER_SOURCE = """
def _cutback_cached_responses(client, *, _cutback_workflow, _cutback_revision,
                              _cutback_contract, **request):
    import os
    scope_file = os.environ.get("CUTBACK_NATIVE_CACHE_SCOPE_FILE")
    if not scope_file:
        return client.responses.create(**request)
    from cutback.native_cache import responses_create_from_scope_file
    return responses_create_from_scope_file(
        client, scope_file=scope_file, expected_workflow=_cutback_workflow,
        expected_request_revision=_cutback_revision,
        expected_contract_sha256=_cutback_contract, **request,
    ).response

"""


def prepare_patch(root, current, site, profile, recommendation, *, proposal_only):
    """Called only after the ordinary recommendation/source/lineage guards pass."""
    binding = profile.binding
    evidence = recommendation["evidence"]
    baseline, candidate = evidence["rollback"]["baseline"], evidence["candidate"]
    if baseline.get("exact_cache") is not None or candidate.get("exact_cache") is None:
        raise ValueError("EXACT_CACHE_SOURCE_REQUIRES_UNCACHED_BASELINE")
    contract = ExactCacheExecution.model_validate(candidate["exact_cache"])
    if {k: v for k, v in baseline.items() if k != "key"} != {
        k: v for k, v in candidate.items() if k not in {"key", "exact_cache"}
    }:
        raise ValueError("EXACT_CACHE_SOURCE_MUST_PRESERVE_THE_NATIVE_REQUEST")
    if evidence.get("exact_cache", {}).get("execution_qualified") is not True:
        raise ValueError("EXACT_CACHE_SOURCE_REQUIRES_CONFIRMED_CACHE_EXECUTION")
    if (
        profile.provider != "openai"
        or profile.request.sdk_surface != "responses.create"
        or binding.model_binding["file"] != site["source_file"]
    ):
        raise ValueError("EXACT_CACHE_SOURCE_REQUIRES_SAME_FILE_NATIVE_RESPONSES")
    request_hash = baseline.get("request_contract_sha256") or fingerprint(
        baseline.get("request_contract")
    )
    instruction_hash = baseline.get("instruction_sha256") or fingerprint(
        baseline.get("instruction", "")
    )
    if (
        request_hash != fingerprint(profile.request.model_dump(mode="json"))
        or instruction_hash != fingerprint(profile.instruction)
        or baseline.get("model") != profile.model
        or baseline.get("max_output_tokens") != profile.max_output_tokens
    ):
        raise ValueError("EXACT_CACHE_EVALUATED_REQUEST_DOES_NOT_MATCH_SOURCE")
    entries = site["entry_points"] or [
        {"file": site["source_file"], "symbol": site["source_symbol"]}
    ]
    affected = {workflow_identity(item["file"], item["symbol"])[0] for item in entries}
    incomplete_codes = {
        "SCAN_TIME_LIMIT",
        "RESOURCE_LIMIT",
        "AST_DEPTH_LIMIT",
        "ANALYSIS_TIME_BUDGET_EXCEEDED",
        "UNPARSED_FILE",
        "IGNORE_FILE_UNREADABLE",
    }
    if affected != {binding.workflow_id} or any(
        warning.get("code") in incomplete_codes or warning.get("file") in binding.dependencies
        for warning in current.get("warnings", [])
    ):
        raise ValueError("EXACT_CACHE_SOURCE_CONSUMER_SCOPE_REQUIRES_SEPARATE_REVIEW")
    path = root / site["source_file"]
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != binding.dependencies[site["source_file"]]:
        raise ValueError("EXACT_CACHE_SOURCE_CHANGED_DURING_REVIEW")
    source = raw.decode("utf-8")
    tree = ast.parse(source)
    if any(
        isinstance(node, ast.Name)
        and node.id.startswith("_cutback_cache")
        or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name.startswith("_cutback_cache")
        or isinstance(node, ast.alias)
        and (node.asname or node.name.split(".")[0]).startswith("_cutback_cache")
        or isinstance(node, ast.arg)
        and node.arg.startswith("_cutback_cache")
        or isinstance(node, ast.ExceptHandler)
        and node.name is not None
        and node.name.startswith("_cutback_cache")
        or isinstance(node, (ast.Global, ast.Nonlocal))
        and any(name.startswith("_cutback_cache") for name in node.names)
        for node in ast.walk(tree)
    ):
        raise ValueError("EXACT_CACHE_SOURCE_HELPER_NAME_CONFLICT")
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and node.lineno == site["line"]
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create"
    ]
    if len(calls) != 1:
        raise ValueError("EXACT_CACHE_SOURCE_CALL_NOT_UNIQUE")
    call = calls[0]
    if (
        not isinstance(call.func, ast.Attribute)
        or not isinstance(call.func.value, ast.Attribute)
        or call.func.value.attr != "responses"
        or not isinstance(call.func.value.value, ast.Name)
        or call.args
        or any(keyword.arg is None for keyword in call.keywords)
    ):
        raise ValueError("EXACT_CACHE_SOURCE_REQUIRES_NAMED_SYNC_CLIENT_AND_KEYWORDS")
    contract_hash = fingerprint(contract.model_dump(mode="json"))
    replacement = ast.Call(
        func=ast.Name(id=HELPER_NAME, ctx=ast.Load()),
        args=[copy.deepcopy(call.func.value.value)],
        keywords=[
            ast.keyword(arg=name, value=ast.Constant(value=value))
            for name, value in (
                ("_cutback_workflow", binding.workflow_id),
                ("_cutback_revision", binding.request_sha256),
                ("_cutback_contract", contract_hash),
            )
        ]
        + copy.deepcopy(call.keywords),
    )
    replacement = ast.copy_location(replacement, call)
    if call.end_lineno is None or call.end_col_offset is None:
        raise ValueError("EXACT_CACHE_SOURCE_RANGE_UNAVAILABLE")
    lines = raw.splitlines(keepends=True)
    start = sum(map(len, lines[: call.lineno - 1])) + call.col_offset
    end = sum(map(len, lines[: call.end_lineno - 1])) + call.end_col_offset
    inserted = 0
    for index, node in enumerate(tree.body):
        if (
            index == 0
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            inserted = index + 1
        elif isinstance(node, ast.ImportFrom) and node.module == "__future__":
            inserted = index + 1
        else:
            break
    offset = (
        sum(map(len, lines[: tree.body[inserted - 1].end_lineno]))
        if inserted
        else (sum(map(len, lines[: tree.body[0].lineno - 1])))
    )
    newline = "\r\n" if b"\r\n" in raw else "\n"
    line = source.splitlines()[call.lineno - 1]
    indentation = line[: len(line) - len(line.lstrip())]
    arguments = [ast.unparse(value) for value in replacement.args]
    arguments.extend(ast.unparse(keyword) for keyword in replacement.keywords)
    rendered = (
        HELPER_NAME
        + "("
        + newline
        + "".join(indentation + "    " + argument + "," + newline for argument in arguments)
        + indentation
        + ")"
    )
    changed = raw[:start] + rendered.encode() + raw[end:]
    changed = changed[:offset] + HELPER_SOURCE.replace("\n", newline).encode() + changed[offset:]
    after = changed.decode("utf-8")

    class ReplaceCall(ast.NodeTransformer):
        def visit_Call(self, node):
            if node.lineno == call.lineno and node.col_offset == call.col_offset:
                return copy.deepcopy(replacement)
            return self.generic_visit(node)

    expected = ReplaceCall().visit(copy.deepcopy(tree))
    expected.body[inserted:inserted] = ast.parse(HELPER_SOURCE).body
    if ast.dump(ast.parse(after)) != ast.dump(expected):
        raise ValueError("EXACT_CACHE_PATCH_CHANGED_UNEVALUATED_APPLICATION_CODE")

    def diff(before, after):
        return "".join(
            line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
            for line in difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile="a/" + site["source_file"],
                tofile="b/" + site["source_file"],
            )
        )

    patch, rollback = diff(source, after), diff(after, source)
    if redact(patch, []) != patch or redact(rollback, []) != rollback:
        raise ValueError("PATCH_CONTEXT_REQUIRES_MINIMAL_PRIVATE_REVIEW")
    from cutback.changes import CanaryPolicy

    artifact = {
        "status": "DRY_RUN_ONLY",
        "review_status": "human_review_required" if proposal_only else "accepted_for_staging",
        "diff": patch,
        "rollback_diff": rollback,
        "diff_sha256": hashlib.sha256(patch.encode()).hexdigest(),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source_file": site["source_file"],
        "call_source_file": site["source_file"],
        "source_binding": binding.model_dump(mode="json"),
        "affected_workflows": sorted(affected),
        "unvalidated_workflows": [],
        "unresolved_consumer_workflows": [],
        "scanner_warning_codes": sorted(
            {warning["code"] for warning in current.get("warnings", [])}
        ),
        "consumer_scope": "scanner_detected_source_only_not_runtime_or_external_consumers",
        "call_site_id": site["call_site_id"],
        "source_commit": current["project"].get("commit"),
        "recommendation_id": recommendation["id"],
        "run_id": recommendation["run_id"],
        "dataset_hash": evidence["dataset_hash"],
        "plan_hash": evidence["plan_hash"],
        "quality_scope": evidence["status"],
        "transformations": ["exact_cache"],
        "production_deployed": False,
        "verification": {
            "kind": "source_integrity_and_syntax_not_business_quality",
            "before_sha256": hashlib.sha256(raw).hexdigest(),
            "after_sha256": hashlib.sha256(changed).hexdigest(),
            "application_tests": "agent_must_inspect_and_run_authorized_application_checks",
        },
        "runtime_cache": {
            "activation": "disabled_until_private_scope_file_is_explicitly_configured",
            "environment_variable": "CUTBACK_NATIVE_CACHE_SCOPE_FILE",
            "contract_sha256": contract_hash,
            "request_revision": binding.request_sha256,
            "workflow": binding.workflow_id,
            "credentials_in_source": False,
            "native_retries": 0,
            "max_native_dispatch_seconds": 30,
            "real_customer_savings_verified": False,
        },
        "staging_steps": [
            "review the diff and run checks on both exact application trees",
            "install the source-matched Cutback runtime before explicitly enabling caching",
            "configure a private scope file and qualify HTTP cache latency and availability",
            "approve staging traffic and inspect later task outcomes before any production change",
        ],
        "canary": CanaryPolicy().model_dump(),
    }
    if evidence.get("native_fixture") is not None:
        artifact["native_fixture"] = evidence["native_fixture"]
    return artifact
