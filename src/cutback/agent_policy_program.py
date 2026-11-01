"""HTTP-only whole-policy preparation and continuation with a metadata-only journal."""

import math
import os
import time
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import TypeAdapter

from cutback.core.models import Identifier
from cutback.core.request_profiles import fingerprint
from cutback.core.workspace import read_private, workspace_lock, write_private

CONTRACT = "cutback.source-policy-agent.v1"
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED", "ABSTAINED"}


def _location(root):
    from cutback.agent import workspace

    root, directory = workspace(Path(root))
    return root, directory, directory / "policy-program.json"


def current(root, control):
    from cutback.policy_source import inspect_configuration

    root, _, path = _location(root)
    saved = read_private(path)
    if (
        saved.get("version") != CONTRACT
        or saved.get("endpoint") != control.endpoint
        or saved.get("root_fingerprint") != fingerprint(str(root))
    ):
        raise ValueError("POLICY_PROGRAM_SAVED_ROOT_OR_COLLECTOR_CHANGED")
    _, source = inspect_configuration(root, saved["source"]["producer_file"])
    if source.model_dump(mode="json") != saved["source"]:
        raise ValueError("POLICY_PROGRAM_SAVED_SOURCE_CHANGED")
    return saved


def prepare(
    root,
    control,
    project_id,
    *,
    dataset_id,
    baseline_snapshot_id,
    producer_file,
    purpose,
    max_usd,
    max_requests=1000,
    max_wall_seconds=600,
    savings_per_success=None,
    forecast_successful_tasks=None,
    minimum=0.9,
    min_test_samples=30,
    min_test_groups=30,
    limit=3,
    generate=True,
    fixture_transport=False,
    cache_contract=None,
):
    """Read the fixed source, upload explicitly authorized policies, freeze a preview.

    Dataset import/content rights and a measured baseline snapshot are prerequisites.
    This operation neither approves the program nor sends a provider request.
    """
    from cutback.core.exact_cache import ExactCacheExecution
    from cutback.core.improvement_policy import SourceImprovementPolicy
    from cutback.core.policy_execution import SourcePolicyExecutionSpec
    from cutback.evals.campaigns import CampaignBudget, NativeGeneratorSpec
    from cutback.evals.experiments import ExperimentPlan, ModelSpec
    from cutback.evals.learned_routing import RoutingFitOptions
    from cutback.evals.metrics import MetricSpec
    from cutback.evals.search import candidate_portfolio
    from cutback.policy_source import inspect_configuration

    root, directory, path = _location(root)
    with workspace_lock(directory):
        project_id, dataset_id = str(UUID(str(project_id))), str(UUID(str(dataset_id)))
        baseline_snapshot_id = str(UUID(str(baseline_snapshot_id)))
        TypeAdapter(Identifier).validate_python(purpose)
        original, binding = inspect_configuration(root, producer_file)
        if original.strategy.provider != "openai" or original.cache_execution is not None:
            raise ValueError("POLICY_PROGRAM_CURRENT_UNCACHED_NATIVE_REFERENCE_REQUIRED")
        ready = control("GET", "/v1/health/ready")
        if ready.get("source_policy_program_contract") != "cutback.source-policy-program.v1":
            raise ValueError("POLICY_PROGRAM_COLLECTOR_UPGRADE_REQUIRED")
        dataset = control("GET", f"/v1/projects/{project_id}/datasets/{dataset_id}")
        if dataset.get("id") != dataset_id or dataset.get("workflow") != original.workflow:
            raise ValueError("POLICY_PROGRAM_DATASET_SOURCE_WORKFLOW_CHANGED")
        if (
            fixture_transport
            and dataset.get("provenance", {}).get("source_type") != "synthetic_fixture"
        ):
            raise ValueError("POLICY_SCRIPTED_EXECUTION_REQUIRES_SYNTHETIC_DATA_AND_RIGHTS")
        budget = CampaignBudget(
            max_usd=max_usd, max_requests=max_requests, max_wall_seconds=max_wall_seconds
        )
        fit = (
            RoutingFitOptions()
            if 2 <= dataset.get("split_counts", {}).get("train", 0) <= 256
            else None
        )
        metric = MetricSpec(
            kind="exact_label",
            minimum=minimum,
            min_test_samples=min_test_samples,
            min_test_groups=min_test_groups,
        )
        if type(limit) is not int or not 1 <= limit <= 5:
            raise ValueError("POLICY_PROGRAM_ONE_TO_FIVE_CONTROLS_REQUIRED")
        cache = (
            ExactCacheExecution.model_validate(cache_contract)
            if cache_contract is not None
            else None
        )
        native_limit = limit - int(cache is not None)
        candidates = (
            candidate_portfolio(original.strategy, limit=native_limit) if native_limit else []
        )
        policies = {"reference": original}
        for candidate in candidates:
            raw = original.model_dump(mode="json")
            raw["strategy"] = candidate.model_dump(mode="json")
            policies[candidate.key] = SourceImprovementPolicy.model_validate(raw)
        if cache is not None:
            raw = original.model_dump(mode="json")
            raw["cache_execution"] = cache.model_dump(mode="json")
            policies["source-exact-cache"] = SourceImprovementPolicy.model_validate(raw)
        transport: Literal["native_provider", "scripted_test_only"] = (
            "scripted_test_only" if fixture_transport else "native_provider"
        )
        generator = (
            NativeGeneratorSpec(
                model=ModelSpec(
                    key="generator",
                    provider="openai",
                    model="gpt-4.1-mini",
                    max_input_tokens=100000,
                    max_output_tokens=4096,
                ),
                transport=transport,
                rounds=2,
                variants_per_round=2,
                max_output_tokens=original.strategy.max_output_tokens,
            )
            if generate
            else None
        )
        intent = {
            "project_id": project_id,
            "dataset_id": dataset_id,
            "dataset_hash": dataset["hash"],
            "baseline_snapshot_id": baseline_snapshot_id,
            "source": binding.model_dump(mode="json"),
            "purpose": purpose,
            "budget": budget.model_dump(mode="json"),
            "fit": fit.model_dump(mode="json") if fit else None,
            "metric": metric.model_dump(mode="json"),
            "candidate_keys": list(policies),
            "cache_contract": cache.model_dump(mode="json") if cache else None,
            "generator": generator.model_dump(mode="json") if generator else None,
            "savings_per_success": str(savings_per_success)
            if savings_per_success is not None
            else None,
            "forecast_successful_tasks": forecast_successful_tasks,
            "transport": transport,
        }
        if path.exists():
            saved = current(root, control)
            if saved.get("intent") != intent:
                raise ValueError("POLICY_PROGRAM_PREPARATION_INTENT_CHANGED")
        else:
            saved = {
                "version": CONTRACT,
                "endpoint": control.endpoint,
                "project_id": project_id,
                "root_fingerprint": fingerprint(str(root)),
                "source": binding.model_dump(mode="json"),
                "workflow": original.workflow,
                "intent": intent,
                "request_id": str(uuid4()),
                "template_request_id": str(uuid4()),
                "content_nonce": str(uuid4()),
                "policy_ids": {key: str(uuid4()) for key in policies},
            }
            write_private(path, saved)
        references = []
        for key, policy in policies.items():
            references.append(
                control(
                    "POST",
                    f"/v1/projects/{project_id}/source-policies",
                    {
                        "request_id": saved["policy_ids"][key],
                        "dataset_id": dataset_id,
                        "source": saved["source"],
                        "key": key,
                        "policy": policy.model_dump(mode="json"),
                        "purpose": purpose,
                    },
                )
            )
        execution = SourcePolicyExecutionSpec(
            source=binding, reference=references[0], candidates=references[1:], transport=transport
        )
        from cutback.evals.policy_models import policy_descriptors

        descriptions = policy_descriptors(execution, policies)
        plan = ExperimentPlan(
            name="source-policy-comparison",
            workflow=original.workflow,
            dataset_hash=dataset["hash"],
            baseline=descriptions["reference"],
            candidates=[descriptions[key] for key in list(policies)[1:]],
            metric=metric,
            budget=intent["budget"],
            approved_providers=["openai"],
            split="validation",
            retain_case_outputs=True,
            content_nonce=saved["content_nonce"],
            evaluator_version="deterministic-v19",
            source_policy_execution=execution,
        )
        template = control(
            "POST",
            f"/v1/projects/{project_id}/experiments/plans",
            {
                "request_id": saved["template_request_id"],
                "dataset_id": dataset_id,
                "specification": plan.model_dump(mode="json"),
            },
        )
        saved["template_plan_id"] = template["id"]
        write_private(path, saved)
        request = {
            "request_id": saved["request_id"],
            "template_plan_id": template["id"],
            "baseline_snapshot_id": baseline_snapshot_id,
            "budget": intent["budget"],
            "conservative_savings_per_success_usd": intent["savings_per_success"],
            "forecast_successful_tasks": forecast_successful_tasks,
            "generator": generator.model_dump(mode="json") if generator else None,
            "fit": fit.model_dump(mode="json") if fit else None,
        }
        row = control("POST", f"/v1/projects/{project_id}/source-policy-programs", request)
        saved.update(program_id=row["id"], program_hash=row["program_hash"])
        write_private(path, saved)
        return row


def status(root, control):
    saved = current(root, control)
    return _status(saved, control)


def _status(saved, control):
    if not saved.get("program_id"):
        raise ValueError("POLICY_PROGRAM_PREPARATION_REPLY_UNCERTAIN_RESUME_SAME_INTENT")
    row = control(
        "GET", f"/v1/projects/{saved['project_id']}/source-policy-programs/{saved['program_id']}"
    )
    execution = row.get("specification", {}).get("template", {}).get("source_policy_execution", {})
    public = execution.get("source", {})
    if (
        row.get("id") != saved["program_id"]
        or row.get("program_hash") != saved["program_hash"]
        or row.get("template_plan_id") != saved["template_plan_id"]
        or any(
            public.get(key) != saved["source"][key]
            for key in (
                "producer_file",
                "configuration_file",
                "commit",
                "source_tree_hash",
                "workflow",
                "max_input_bytes",
            )
        )
    ):
        raise ValueError("POLICY_PROGRAM_COLLECTOR_IDENTITY_OR_SOURCE_CHANGED")
    return row


def archive(root, control):
    """Retire a fully accounted terminal identity before preparing another source revision.

    A changed application may archive old metadata, but cannot revive old approval or
    erase an unresolved bill. Both the local identity and collector receipt must match.
    """
    root, directory, path = _location(root)
    with workspace_lock(directory):
        saved = read_private(path)
        if (
            saved.get("version") != CONTRACT
            or saved.get("endpoint") != control.endpoint
            or saved.get("root_fingerprint") != fingerprint(str(root))
        ):
            raise ValueError("POLICY_PROGRAM_SAVED_ROOT_OR_COLLECTOR_CHANGED")
        row = _status(saved, control)
        if (
            row["status"] not in TERMINAL
            or row.get("billing_complete") is not True
            or row["requests"] != row["known_cost_requests"]
        ):
            raise ValueError("POLICY_PROGRAM_TERMINAL_KNOWN_ACCOUNTING_REQUIRED_BEFORE_ARCHIVE")
        identifier = str(UUID(saved["program_id"]))
        destination = directory / ("policy-program-archived-" + identifier + ".json")
        # Exclusive creation prevents overwriting an older audit identity. A crash
        # between creation and unlink is reconciled only if those bytes still match.
        if destination.exists():
            if read_private(destination) != saved:
                raise ValueError("POLICY_PROGRAM_ARCHIVED_IDENTITY_CHANGED")
        else:
            write_private(destination, saved, exclusive=True)
        os.unlink(path)
        return {
            "status": "ARCHIVED",
            "program_id": identifier,
            "provider_calls": 0,
            "approval_transferred": False,
        }


def advance(root, control, *, program_hash=None, approval_ref=None):
    _, directory, path = _location(root)
    with workspace_lock(directory):
        saved, row = current(root, control), status(root, control)
        if row["status"] != "READY_FOR_APPROVAL":
            return row
        if saved.get("approval_dispatched"):
            return {**row, "operator_code": "APPROVAL_REPLY_UNCERTAIN_RECONCILE_SAME_PROGRAM"}
        if program_hash != saved["program_hash"] or not approval_ref:
            raise ValueError("EXACT_POLICY_PROGRAM_HASH_AND_APPROVAL_REFERENCE_REQUIRED")
        TypeAdapter(Identifier).validate_python(approval_ref)
        saved["approval_dispatched"] = True
        write_private(path, saved)
        return control(
            "POST",
            f"/v1/projects/{saved['project_id']}/source-policy-programs/{saved['program_id']}/approve",
            {"program_hash": program_hash, "approval_ref": approval_ref},
        )


def run(root, control, *, max_wait_seconds=300, poll_seconds=1, max_polls=300, emit=None):
    if (
        type(max_wait_seconds) is not int
        or not 1 <= max_wait_seconds <= 3600
        or type(max_polls) is not int
        or not 1 <= max_polls <= 3600
        or type(poll_seconds) not in {int, float}
        or not math.isfinite(poll_seconds)
        or not 0.1 <= poll_seconds <= 10
    ):
        raise ValueError("POLICY_PROGRAM_POLL_AND_WAIT_BOUNDS_REQUIRED")
    stop, previous = time.monotonic() + max_wait_seconds, None
    for _ in range(max_polls):
        row = status(root, control)
        identity = row["status"], row["requests"], row["known_cost_requests"]
        if emit and identity != previous:
            emit(
                {
                    key: row[key]
                    for key in (
                        "id",
                        "program_hash",
                        "status",
                        "requests",
                        "known_cost_requests",
                        "reserved_usd",
                        "known_spend_subtotal_usd",
                    )
                }
            )
        previous = identity
        if row["status"] in TERMINAL or row["approval_status"] != "consumed":
            return row
        remaining = stop - time.monotonic()
        if remaining <= 0:
            return {**row, "operator_code": "WAIT_BOUND_REACHED_RESUME_SAME_PROGRAM"}
        time.sleep(min(poll_seconds, remaining))
    return {**row, "operator_code": "POLL_BOUND_REACHED_RESUME_SAME_PROGRAM"}


def proposal(root, control, *, purpose):
    from cutback.policy_changes import SourcePolicyProposal, prepare_patch

    root, directory, _ = _location(root)
    with workspace_lock(directory):
        saved = current(root, control)
        TypeAdapter(Identifier).validate_python(purpose)
        value = SourcePolicyProposal.model_validate(
            control(
                "POST",
                f"/v1/projects/{saved['project_id']}/source-policy-programs/{saved['program_id']}/source-proposal",
                {"purpose": purpose},
            )
        )
        if value.binding.model_dump(mode="json") != saved["source"]:
            raise ValueError("POLICY_PROGRAM_CONFIRMED_SOURCE_CHANGED")
        artifact = prepare_patch(root, value)
        write_private(directory / "policy-program-proposal.json", artifact)
        return artifact


def review(root, control, *, purpose, contract, receipt, proposed_root, coverage, branch=None):
    from cutback.policy_changes import review_change

    artifact = proposal(root, control, purpose=purpose)
    root, directory, _ = _location(root)
    with workspace_lock(directory):
        current(root, control)
        result = review_change(
            artifact, contract, receipt, root, proposed_root, coverage, branch=branch
        )
        write_private(directory / "policy-program-review.json", result)
        return result
