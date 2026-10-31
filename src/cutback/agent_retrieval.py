"""Resumable preparation for the qualified local-corpus retrieval workflow."""

import hashlib
import json
from uuid import UUID, uuid4

from cutback.core.request_profiles import fingerprint


def component_frontier(report):
    """Compare native API plus declared retrieval CPU, preserving unknown totals."""
    from decimal import Context, localcontext

    from cutback.evals.search import sample_frontier

    totals = report.get("retrieval", {}).get("models", {})
    models = []
    for model in report.get("models", []):
        cost = totals.get(model["model"]["key"], {}).get("modeled_component_cost_usd")
        models.append({**model, "cost_complete": cost is not None, "modeled_cost_usd": cost})
    decisions = [
        {**row, "quality_gate_passed": row.get("retrieval_automation_eligible") is True}
        for row in report.get("recommendations", [])
    ]
    with localcontext(Context(prec=80)):
        result = sample_frontier({**report, "models": models, "recommendations": decisions})
    return {
        **result,
        "economics_scope": "native_api_plus_declared_cpu_components_not_full_task_or_invoice",
        "latency_scope": "native_request_only_not_end_to_end_retrieval",
    }


def regression_gate(report, *, expected_plan_hash, candidate=None, sample_only=False):
    """Require v6 retrieval component economics and independent confirmation lineage."""
    reasons = []
    if report.get("status") != "COMPLETED" or report.get("billing_complete") is not True:
        reasons.append("run_or_accounting_incomplete")
    if report.get("plan", {}).get("plan_hash") != expected_plan_hash:
        reasons.append("frozen_plan_mismatch")
    if report.get("plan", {}).get("split") != "test":
        reasons.append("independent_test_confirmation_required")
    if report.get("native_fixture") and not sample_only:
        reasons.append("fixture_transport_cannot_verify_real_model_quality")
    if report.get("retrieval", {}).get("version") != "cutback.retrieval-execution.v1":
        reasons.append("retrieval_execution_contract_required")
    if report.get("lineage", {}).get("fresh_test_claim") is not True:
        reasons.append("independent_test_lineage_unknown_or_exposed")
    qualified = [
        row
        for row in report.get("recommendations", [])
        if (candidate is None or row["candidate"]["key"] == candidate)
        and row.get("evaluator_version") == "deterministic-v6"
        and row.get("retrieval_automation_eligible") is True
        and row.get("quality_gate_passed") is True
        and row.get("cheaper_on_sample") is True
    ]
    if not sample_only:
        qualified = [
            row
            for row in qualified
            if row.get("evidence_tier") == 4
            and row.get("statistical_gate_passed") is True
            and row.get("status") == "VERIFIED_ON_HELDOUT_DATASET"
        ]
    if not qualified:
        reasons.append("no_retrieval_candidate_meets_component_cost_and_quality_gates")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "scope": "sample_only" if sample_only else "independent_heldout_dataset",
        "candidate_keys": sorted(row["candidate"]["key"] for row in qualified)
        if not reasons
        else [],
        "plan_hash": expected_plan_hash,
        "production_deployed": False,
        "production_savings_usd": None,
    }


def selected_source(root, state, workflow=None):
    from cutback.retrieval_source import inspect_producer

    choices = [
        row
        for row in state.get("retrieval_producers", [])
        if workflow is None or row["workflow_id"] == workflow
    ]
    if len(choices) != 1:
        raise ValueError("SELECT_ONE_QUALIFIED_RETRIEVAL_WORKFLOW")
    value = choices[0]
    _, binding, producer = inspect_producer(root, value["producer_binding"]["file"])
    if (
        binding.model_dump(mode="json") != value["configuration_binding"]
        or producer.model_dump(mode="json") != value["producer_binding"]
    ):
        raise ValueError("RETRIEVAL_SOURCE_CHANGED_REANALYZE")
    return value


def confirm(root, control, *, candidate, max_usd, max_requests, purpose):
    """Reconcile one nonbillable frozen confirmation; never approve or queue it."""
    from cutback.agent import load_session, public_session, save_session, workspace
    from cutback.core.workspace import workspace_lock
    from cutback.evals.experiments import Budget, ExperimentPlan, plan_summary

    if load_session(root).get("search_mode") == "joint_model_retrieval":
        from cutback.agent_joint_retrieval import confirm as joint_confirm

        return joint_confirm(
            root,
            control,
            candidate=candidate,
            max_usd=max_usd,
            max_requests=max_requests,
            purpose=purpose,
        )

    budget = Budget(max_usd=max_usd, max_requests=max_requests)
    root, directory = workspace(root)
    with workspace_lock(directory):
        state = load_session(root)
        if state.get("endpoint") != control.endpoint:
            raise ValueError("COLLECTOR_DESTINATION_MISMATCH")
        selected_source(root, state, state["selected_workflow"])
        if state["phase"] == "COMPLETED" and not state.get("confirmation"):
            decision: dict = next(
                (row for row in state.get("decisions", []) if row["candidate"]["key"] == candidate),
                {},
            )
            if decision.get("retrieval_automation_eligible") is not True:
                raise ValueError("RETRIEVAL_COMPONENT_COST_AND_QUALITY_GATES_REQUIRED")
            state["confirmation"] = {
                "search_run_id": state["run_id"],
                "search_plan_id": state["plan"]["id"],
                "search_plan_hash": state["plan"]["plan_hash"],
                "selected_candidate": candidate,
                "request_id": str(uuid4()),
                "budget": budget.model_dump(mode="json"),
            }
            save_session(directory, state, "PREPARING_CONFIRMATION")
        if state["phase"] != "PREPARING_CONFIRMATION" or not state.get("confirmation"):
            raise ValueError("COMPLETED_SEARCH_OR_SAME_CONFIRMATION_PREPARATION_REQUIRED")
        frozen = state["confirmation"]
        if candidate != frozen["selected_candidate"] or budget != Budget.model_validate(
            frozen["budget"]
        ):
            raise ValueError("FROZEN_CONFIRMATION_INTENT_CHANGED")
        base = f"/v1/projects/{state['project_id']}"
        content = control(
            "POST",
            base + f"/experiments/plans/{frozen['search_plan_id']}/content",
            {"purpose": purpose},
        )
        if content["plan_hash"] != frozen["search_plan_hash"]:
            raise ValueError("FROZEN_SEARCH_PLAN_CHANGED")
        prior = ExperimentPlan.model_validate(content["specification"])
        selected = [value for value in prior.candidates if value.key == candidate]
        if (
            prior.retrieval_execution is None
            or prior.evaluator_version != "deterministic-v6"
            or prior.split != "validation"
            or prior.predecessor_run_id
            or len(selected) != 1
        ):
            raise ValueError("EXACT_RETRIEVAL_VALIDATION_CANDIDATE_REQUIRED")
        raw = prior.model_dump(mode="json")
        raw.update(
            split="test",
            sample_limit=None,
            predecessor_run_id=frozen["search_run_id"],
            candidates=[selected[0].model_dump(mode="json")],
            budget=budget.model_dump(mode="json"),
        )
        raw["retrieval_execution"]["variants"] = [
            value.model_dump(mode="json")
            for value in prior.retrieval_execution.variants
            if value.model_key in {prior.baseline.key, candidate}
        ]
        specification = ExperimentPlan.model_validate(raw)
        summary = plan_summary(
            specification,
            {
                "immutable_hash": specification.dataset_hash,
                "items": [{"split": "test"}] * state["dataset_split_counts"].get("test", 0),
            },
        )
        if not summary["fits_budget"]:
            raise ValueError("CONFIRMATION_EXCEEDS_BUDGET_OR_HAS_NO_TEST_GROUPS")
        planned = control(
            "POST",
            base + "/experiments/plans",
            {
                "request_id": frozen["request_id"],
                "dataset_id": state["dataset_id"],
                "specification": specification.model_dump(mode="json"),
            },
        )
        if planned["id"] != frozen["request_id"]:
            raise ValueError("CONFIRMATION_PREPARATION_IDENTITY_CHANGED")
        state["plan"] = planned
        for name in ("run_id", "report", "decisions", "search_results", "proposal"):
            state.pop(name, None)
        save_session(directory, state, "PLAN_READY")
        return public_session(state)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("RETRIEVAL_CORPUS_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def prepare(
    root,
    control,
    project,
    *,
    files,
    rights_basis,
    rights_ref,
    max_usd,
    workflow,
    max_requests,
    minimum,
    independent_labels,
    representative,
    retain_case_outputs,
    mapping,
    assumed_usd_per_cpu_hour,
    min_test_samples,
    corpus_authorized,
    joint=False,
):
    from cutback.agent import load_session, permitted_file, public_session, save_session, workspace
    from cutback.agent_preparation import begin, bind, expiry
    from cutback.core.retrieval import (
        FRAME_INSTRUCTION,
        RetrievalConfigurationBinding,
        RetrievalCorpus,
    )
    from cutback.core.retrieval_producer import RetrievalProducerBinding
    from cutback.core.workspace import read_private, workspace_lock
    from cutback.evals.datasets import CapturePolicy, DatasetImport, prepare_dataset
    from cutback.evals.experiments import Budget, ExperimentPlan, ModelSpec, plan_summary
    from cutback.evals.metrics import MetricSpec
    from cutback.evals.records import read_records
    from cutback.evals.retrieval_joint import JOINT_RETRIEVAL_CONTRACT
    from cutback.evals.retrieval_planning import draft_retrieval_plan

    if corpus_authorized is not True:
        raise ValueError("EXPLICIT_BOUND_RETRIEVAL_CORPUS_AUTHORIZATION_REQUIRED")
    if type(joint) is not bool:
        raise ValueError("JOINT_RETRIEVAL_PREPARATION_FLAG_MUST_BE_BOOLEAN")
    mode = "joint_model_retrieval" if joint else "retrieval_search"
    root, directory = workspace(root)
    project = str(UUID(str(project)))
    with workspace_lock(directory):
        state = load_session(root)
        if state["phase"] not in {"ANALYZED", "SOURCE_CHANGED", "PLAN_READY", "PREPARING"}:
            raise ValueError("WORKFLOW_PREPARATION_ALREADY_STARTED_INSPECT_STATUS")
        source = selected_source(root, state, workflow)
        if not independent_labels or minimum is None:
            raise ValueError("RETRIEVAL_SEARCH_REQUIRES_EXPLICIT_INDEPENDENT_QUALITY_CRITERIA")
        if not files or len(files) > 20:
            raise ValueError("ONE_TO_TWENTY_PERMITTED_FILES_REQUIRED")
        preparation = begin(directory, state, "native")
        policy = CapturePolicy(
            enabled=True,
            approval_ref=rights_ref,
            rights_basis=rights_basis,
            environments=["staging"],
            workflows=[source["workflow_id"]],
            allowed_providers=["openai"],
            expires_at=expiry(preparation),
            retention_days=1,
            sample_rate=1,
            max_samples=10000,
            max_payload_bytes=512000,
        )
        producer = RetrievalProducerBinding.model_validate(source["producer_binding"])
        binding = RetrievalConfigurationBinding.model_validate(source["configuration_binding"])
        corpus_path = permitted_file(root, producer.corpus_file)
        if corpus_path.stat().st_size > 512000:
            raise ValueError("RETRIEVAL_CORPUS_SOURCE_BOUND")
        corpus_raw = corpus_path.read_bytes()
        try:
            corpus = RetrievalCorpus.model_validate(
                json.loads(corpus_raw.decode("utf-8"), object_pairs_hook=_unique)
            )
        except (ValueError, UnicodeError, RecursionError):
            raise ValueError("RETRIEVAL_CORPUS_INVALID_OR_OUTSIDE_BOUNDS") from None
        cases, identities = [], []
        for name in files:
            path = permitted_file(root, name)
            raw = path.read_bytes()
            cases.extend(read_records(path, mapping=mapping))
            if path.read_bytes() != raw:
                raise ValueError("RETRIEVAL_EXAMPLES_CHANGED_DURING_PREPARATION")
            identities.append({"path": name, "sha256": hashlib.sha256(raw).hexdigest()})
        body = DatasetImport(
            split_policy="task-aware-v2",
            workflow=source["workflow_id"],
            policy_id=str(uuid4()),
            source_type="synthetic_fixture"
            if rights_basis == "synthetic"
            else "application_records",
            provenance_ref=rights_ref,
            independent_labels=True,
            representative=representative,
            cases=cases,
        )
        prepared = prepare_dataset(body)
        metric = MetricSpec(
            kind="exact_label",
            minimum=minimum,
            min_test_samples=min_test_samples,
            min_test_groups=min_test_samples,
        )
        budget = Budget(max_usd=max_usd, max_requests=max_requests)
        intent = bind(
            fingerprint(
                {
                    "source": source,
                    "examples": identities,
                    "corpus_sha256": hashlib.sha256(corpus_raw).hexdigest(),
                    "rights": rights_ref,
                    "rights_basis": rights_basis,
                    "project": project,
                    "endpoint": control.endpoint,
                    "metric": metric.model_dump(mode="json"),
                    "budget": budget.model_dump(mode="json"),
                    "cpu_basis": assumed_usd_per_cpu_hour,
                    "representative": representative,
                    "retain_case_outputs": retain_case_outputs,
                    **({"search_contract": JOINT_RETRIEVAL_CONTRACT} if joint else {}),
                }
            ),
            preparation,
            body,
            policy,
        )
        if state["phase"] in {"PLAN_READY", "PREPARING"}:
            if state.get("search_mode") != mode or state.get("preparation_sha256") != intent:
                raise ValueError("FROZEN_PLAN_CHANGED_START_NEW_SESSION")
            if state["phase"] == "PLAN_READY":
                return public_session(state)
        bound = max(
            8192,
            1024 * 6
            + binding.settings.max_context_bytes
            + len(FRAME_INSTRUCTION.encode())
            + 128
            + 4096,
        )
        baseline = ModelSpec(
            key="baseline",
            provider="openai",
            model=producer.model,
            max_output_tokens=producer.max_output_tokens,
            max_input_tokens=bound,
        )
        metadata = {
            "workflow": body.workflow,
            "hash": prepared["immutable_hash"],
            "provenance": {
                "independent_labels": True,
                "max_input_bytes": max(
                    (len(case.input.encode("utf-8")) for case in cases), default=0
                ),
            },
        }

        def freeze_nonce(value):
            return (
                value.model_copy(update={"content_nonce": UUID(preparation["content_nonce"])})
                if preparation
                else value
            )

        if joint:
            from cutback.evals.joint_retrieval_planning import draft_plan

            specification = None
            qualified_candidates = 0
            for limit in range(5, 0, -1):
                proposed = freeze_nonce(
                    draft_plan(
                        metadata,
                        baseline,
                        corpus,
                        binding.settings,
                        metric=metric,
                        budget=budget,
                        max_candidates=limit,
                        assumed_usd_per_cpu_hour=assumed_usd_per_cpu_hour,
                        retain_case_outputs=retain_case_outputs,
                        configuration_binding=binding,
                        producer_binding=producer,
                    )
                )
                if limit == 5:
                    qualified_candidates = len(proposed.candidates)
                if plan_summary(proposed, prepared)["fits_budget"]:
                    specification = proposed
                    break
            if specification is None:
                raise ValueError(
                    "PROPOSED_RETRIEVAL_PLAN_EXCEEDS_BUDGET_OR_HAS_NO_VALIDATION_CASES"
                )
        else:
            specification = freeze_nonce(
                draft_retrieval_plan(
                    metadata,
                    baseline,
                    corpus,
                    binding.settings,
                    metric=metric,
                    budget=budget,
                    assumed_usd_per_cpu_hour=assumed_usd_per_cpu_hour,
                    retain_case_outputs=retain_case_outputs,
                    configuration_binding=binding,
                )
            )
        if not plan_summary(specification, prepared)["fits_budget"]:
            raise ValueError("PROPOSED_RETRIEVAL_PLAN_EXCEEDS_BUDGET_OR_HAS_NO_VALIDATION_CASES")
        selected_source(root, state, source["workflow_id"])
        if corpus_path.read_bytes() != corpus_raw:
            raise ValueError("RETRIEVAL_CORPUS_CHANGED_DURING_PREPARATION")
        state.update(
            project_id=project,
            endpoint=control.endpoint,
            selected_workflow=body.workflow,
            preparation_sha256=intent,
            search_mode=mode,
            permitted_files=identities,
            rights_ref=rights_ref,
            quality_scope="heldout_independent_exact_answers",
        )
        if joint:
            state["candidate_preparation"] = {
                "requested_candidate_limit": 5,
                "qualified_candidate_count": qualified_candidates,
                "prepared_candidates": len(specification.candidates),
                "budget_reduced_portfolio": len(specification.candidates) < qualified_candidates,
                "selection_basis": "deterministic_data_independent_prefix_fitting_native_budget",
                "examples_or_quality_requirements_reduced": False,
                "full_task_economics_known": False,
            }
        state["preparation_command"] = dict(
            files=files,
            rights_basis=rights_basis,
            rights_ref=rights_ref,
            max_usd=str(max_usd),
            max_requests=max_requests,
            workflow=body.workflow,
            minimum=minimum,
            independent_labels=independent_labels,
            representative=representative,
            retain_case_outputs=retain_case_outputs,
            mapping=mapping,
            retrieval=not joint,
            joint_retrieval=joint,
            retrieval_corpus_authorized=True,
            assumed_usd_per_cpu_hour=assumed_usd_per_cpu_hour,
            min_test_samples=min_test_samples,
        )
        save_session(directory, state, "PREPARING")
        from cutback import agent_preparation as setup

        setup.require_protocol(control)
        if joint:
            ready = control("GET", "/v1/health/ready")
            if (
                ready.get("joint_retrieval_contract") != JOINT_RETRIEVAL_CONTRACT
                or ready.get("retrieval_recipe_sha256") != binding.retrieval_contract_sha256
            ):
                raise ValueError("COLLECTOR_JOINT_RETRIEVAL_UPGRADE_REQUIRED")
        base = f"/v1/projects/{project}"
        setup.create(
            control,
            base,
            "scans",
            read_private(directory / "inventory.json"),
            preparation,
            state=state,
            directory=directory,
            response_key="scan_id",
        )
        created_policy = setup.create(
            control,
            base,
            "capture-policies",
            policy.model_dump(mode="json"),
            preparation,
            state=state,
            directory=directory,
        )
        state["policy_id"] = created_policy["id"]
        save_session(directory, state)
        dataset = setup.create(
            control,
            base,
            "datasets",
            body.model_copy(update={"policy_id": created_policy["id"]}).model_dump(mode="json"),
            preparation,
            state=state,
            directory=directory,
        )
        state["dataset_id"], state["dataset_split_counts"] = (
            dataset["id"],
            dataset["provenance"]["split_counts"],
        )
        save_session(directory, state)
        specification = ExperimentPlan.model_validate(
            {**specification.model_dump(mode="json"), "dataset_hash": dataset["immutable_hash"]}
        )
        state["plan"] = setup.plan(
            control,
            base,
            dataset["id"],
            specification,
            preparation,
            state=state,
            directory=directory,
        )
        save_session(directory, state, "PLAN_READY")
        return public_session(state)
