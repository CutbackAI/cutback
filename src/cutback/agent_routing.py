"""Source-bound routing preparation and durable stage transitions for application agents."""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from cutback.core.request_profiles import fingerprint
from cutback.core.workspace import read_private, workspace_lock, write_private


def selected_source(root, state, workflow=None):
    from cutback.routing_changes import inspect_configuration

    choices = [
        value
        for value in state.get("routing_producers", [])
        if workflow is None or value["workflow_id"] == workflow
    ]
    if len(choices) != 1:
        raise ValueError("SELECT_ONE_QUALIFIED_ROUTING_WORKFLOW")
    value = choices[0]
    baseline, source = inspect_configuration(root, value["source"]["producer_file"])
    if source.model_dump(mode="json") != value["source"]:
        raise ValueError("ROUTING_SOURCE_CHANGED_REANALYZE")
    return baseline, source, value["workflow_id"]


def next_action(state):
    phase = state["phase"]
    if phase == "PLAN_READY":
        return {
            "code": "REVIEW_EXACT_ROUTING_STAGE_BUDGET",
            "stage": state["routing"]["stage"],
            "tool": "cutback workflow continue",
            "requires": ["exact_plan_hash", "new_stage_approval_reference"],
            "plan": state["plan"],
            "effects": ["one_bounded_native_stage"],
            "automatic_billable_retry": False,
        }
    if phase in {
        "PREPARING_ROUTING_FIT",
        "PREPARING_ROUTING_STAGE",
        "APPROVING",
        "QUEUEING",
        "QUEUED",
        "RUNNING",
    }:
        return {
            "code": state.get("stop_code", "RESUME_SAME_ROUTING_STAGE"),
            "tool": "cutback workflow continue",
            "requires": [],
            "automatic_billable_retry": False,
        }
    if phase in {"COMPLETED", "PROPOSAL_READY"}:
        return {
            "code": "PREPARE_EXACT_ROUTING_DIFF_AND_APPLICATION_CHECKS",
            "tool": "cutback workflow proposal",
            "requires": ["purpose_reference", "protected_output_file"],
            "provider_calls": 0,
            "full_task_cost_known": False,
        }
    return {"code": state.get("stop_code", "INSPECT_ROUTING_STATE"), "provider_calls": 0}


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
    mapping,
    min_test_samples,
    fixture_transport=False,
):
    from cutback.agent import load_session, permitted_file, public_session, save_session, workspace
    from cutback.core.input_routing import (
        SOURCE_DATASET_CASE_BOUND,
        SOURCE_DATASET_CONTENT_BOUND,
    )
    from cutback.evals.datasets import CapturePolicy, DatasetImport, prepare_dataset
    from cutback.evals.experiments import Budget, ExperimentPlan, plan_summary
    from cutback.evals.metrics import MetricSpec
    from cutback.evals.records import read_records
    from cutback.evals.search import candidate_portfolio
    from cutback.routing_changes import validate_source_models

    root, directory = workspace(root)
    project = str(UUID(str(project)))
    with workspace_lock(directory):
        state = load_session(root)
        if state["phase"] not in {"ANALYZED", "SOURCE_CHANGED", "PLAN_READY"}:
            raise ValueError("ROUTING_PREPARATION_ALREADY_STARTED_INSPECT_STATUS")
        baseline, source, workflow_id = selected_source(root, state, workflow)
        if not independent_labels or minimum is None:
            raise ValueError("ROUTING_REQUIRES_EXPLICIT_INDEPENDENT_QUALITY_CRITERIA")
        if fixture_transport and rights_basis != "synthetic":
            raise ValueError("ROUTING_FIXTURE_TRANSPORT_REQUIRES_SYNTHETIC_RIGHTS")
        if not files or len(files) > 20:
            raise ValueError("ONE_TO_TWENTY_PERMITTED_FILES_REQUIRED")
        policy = CapturePolicy(
            enabled=True,
            approval_ref=rights_ref,
            rights_basis=rights_basis,
            environments=["staging"],
            workflows=[workflow_id],
            allowed_providers=["openai"],
            expires_at=datetime.now(UTC) + timedelta(days=1),
            retention_days=1,
            sample_rate=1,
            max_samples=4000,
            max_payload_bytes=512000,
        )
        cases, identities = [], []
        example_bytes = 0
        for name in files:
            path = permitted_file(root, name)
            if path.stat().st_size > 1048576:
                raise ValueError("EXAMPLE_FILE_UNSAFE_OR_EXCEEDS_ONE_MIB")
            raw = path.read_bytes()
            example_bytes += len(raw)
            if example_bytes > SOURCE_DATASET_CONTENT_BOUND:
                raise ValueError("DATASET_READ_CONTENT_BOUND")
            cases.extend(read_records(path, mapping=mapping))
            if len(cases) > SOURCE_DATASET_CASE_BOUND:
                raise ValueError("DATASET_READ_CASE_BOUND")
            if path.read_bytes() != raw:
                raise ValueError("ROUTING_EXAMPLES_CHANGED_DURING_PREPARATION")
            identities.append({"path": name, "sha256": hashlib.sha256(raw).hexdigest()})
        if any(not isinstance(case.gold, str) or not case.gold for case in cases):
            raise ValueError("ROUTING_EXACT_LABEL_REQUIRES_EXPLICIT_STRING_LABELS")
        body = DatasetImport(
            split_policy="task-aware-v2",
            workflow=workflow_id,
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
        # Native catalog variants preserve this source's instruction and bounds.
        candidates = candidate_portfolio(baseline)[:4]
        validate_source_models(source, baseline, candidates)
        metric = MetricSpec(
            kind="exact_label",
            minimum=minimum,
            max_regression=0,
            min_test_samples=min_test_samples,
            min_test_groups=min_test_samples,
        )
        budget = Budget(max_usd=max_usd, max_requests=max_requests)
        from cutback.core.input_routing import RoutingTrainingExecution

        plan = ExperimentPlan(
            name="source-routing-training",
            workflow=workflow_id,
            dataset_hash=prepared["immutable_hash"],
            baseline=baseline,
            candidates=candidates,
            metric=metric,
            budget=budget,
            split="train",
            evaluator_version="deterministic-v16",
            content_nonce=uuid4(),
            retain_case_outputs=True,
            approved_providers=["openai"],
            routing_execution=RoutingTrainingExecution(
                source=source,
                transport="scripted_test_only" if fixture_transport else "native_provider",
            ),
        )
        if not plan_summary(plan, prepared)["fits_budget"]:
            raise ValueError("ROUTING_TRAINING_EXCEEDS_STAGE_CEILING_OR_HAS_NO_TRAIN_CASES")
        # Check every split before any upload; no unhandled rows may be dropped.
        if any(len(case.input.encode()) > source.max_input_bytes for case in cases):
            raise ValueError("ROUTING_SOURCE_INPUT_OUTSIDE_APPROVED_APPLICATION_BOUND")
        intent = fingerprint(
            {
                "source": source.model_dump(mode="json"),
                "examples": identities,
                "rights_basis": rights_basis,
                "rights_ref": rights_ref,
                "project": project,
                "endpoint": control.endpoint,
                "budget": budget.model_dump(mode="json"),
                "metric": metric.model_dump(mode="json"),
                "representative": representative,
                "fixture_transport": fixture_transport,
                "mapping": mapping,
            }
        )
        if state["phase"] == "PLAN_READY":
            if (
                state.get("search_mode") != "source_routing"
                or state.get("preparation_sha256") != intent
            ):
                raise ValueError("FROZEN_ROUTING_INTENT_CHANGED_START_NEW_SESSION")
            return public_session(state)
        selected_source(root, state, workflow_id)
        request = {
            "request_id": str(uuid4()),
            "content_nonce": str(plan.content_nonce),
            "baseline": baseline.model_dump(mode="json"),
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
            "metric": metric.model_dump(mode="json"),
            "budget": budget.model_dump(mode="json"),
            "source": source.model_dump(mode="json"),
            "transport": "scripted_test_only" if fixture_transport else "native_provider",
        }
        write_private(directory / "routing-training-request.json", request)
        state.update(
            search_mode="source_routing",
            project_id=project,
            endpoint=control.endpoint,
            selected_workflow=workflow_id,
            permitted_files=identities,
            preparation_sha256=intent,
            routing={
                "stage": "training",
                "purpose": rights_ref,
                "budget": budget.model_dump(mode="json"),
            },
        )
        save_session(directory, state, "PREPARING")
        base = f"/v1/projects/{project}"
        # These writes make no provider call. An uncertain policy/dataset creation
        # stops in PREPARING, rather than silently creating duplicate imports.
        created_policy = control("POST", base + "/capture-policies", policy.model_dump(mode="json"))
        state["policy_id"] = created_policy["id"]
        save_session(directory, state)
        dataset = control(
            "POST",
            base + "/datasets",
            body.model_copy(update={"policy_id": created_policy["id"]}).model_dump(mode="json"),
        )
        state["dataset_id"] = dataset["id"]
        state["dataset_split_counts"] = dataset["provenance"]["split_counts"]
        request["dataset_id"] = dataset["id"]
        write_private(directory / "routing-training-request.json", request)
        save_session(directory, state, "PREPARING_ROUTING_STAGE")
        state["plan"] = control("POST", base + "/routing/training-drafts", request)
        save_session(directory, state, "PLAN_READY")
        return public_session(state)


def _prepare_next(root, directory, state, control):
    from cutback.agent import save_session

    route = state["routing"]
    base = f"/v1/projects/{state['project_id']}"
    if state["phase"] == "PREPARING_ROUTING_FIT":
        request = route["fit_request"]
        fit = control("POST", base + "/routing/fits", request)
        route["fit_id"] = fit["id"]
        route["stage"] = "validation"
        route["stage_request"] = {
            "request_id": str(uuid4()),
            "content_nonce": str(uuid4()),
            "purpose": route["purpose"],
            "budget": route["budget"],
        }
        save_session(directory, state, "PREPARING_ROUTING_STAGE")
    if state["phase"] == "PREPARING_ROUTING_STAGE":
        selected_source(root, state, state["selected_workflow"])
        suffix = (
            "/routing/training-drafts"
            if route["stage"] == "training"
            else (f"/routing/fits/{route['fit_id']}/{route['stage']}-drafts")
        )
        request = (
            read_private(directory / "routing-training-request.json")
            if route["stage"] == "training"
            else route["stage_request"]
        )
        state["plan"] = control("POST", base + suffix, request)
        for key in ("run_id", "report", "decisions", "stop_code"):
            state.pop(key, None)
        save_session(directory, state, "PLAN_READY")


def advance(root, control, *, plan_hash=None, approval_ref=None):
    from pydantic import TypeAdapter

    from cutback.agent import load_session, public_session, save_session, workspace
    from cutback.core.models import Identifier

    root, directory = workspace(root)
    with workspace_lock(directory):
        state = load_session(root)
        if state["endpoint"] != control.endpoint:
            raise ValueError("COLLECTOR_DESTINATION_MISMATCH")
        base = f"/v1/projects/{state['project_id']}"
        route = state["routing"]
        if state["phase"] in {"PREPARING_ROUTING_FIT", "PREPARING_ROUTING_STAGE"}:
            _prepare_next(root, directory, state, control)
            return public_session(state)
        if state["phase"] == "PLAN_READY":
            selected_source(root, state, state["selected_workflow"])
            if plan_hash != state["plan"]["plan_hash"] or not approval_ref:
                raise ValueError("EXACT_PLAN_HASH_AND_APPROVAL_REFERENCE_REQUIRED")
            TypeAdapter(Identifier).validate_python(approval_ref)
            state["provider_calls"] = None
            save_session(directory, state, "APPROVING")
            control(
                "POST",
                base + f"/experiments/plans/{state['plan']['id']}/approve",
                {"plan_hash": plan_hash, "approval_ref": approval_ref},
            )
            save_session(directory, state, "QUEUEING")
            queued = control("POST", base + "/experiments", {"plan_id": state["plan"]["id"]})
            state["run_id"] = str(UUID(queued["id"]))
            save_session(directory, state, "QUEUED")
        if state["phase"] in {"APPROVING", "QUEUEING"}:
            current = control("GET", base + f"/experiments/plans/{state['plan']['id']}/execution")
            if current["plan_id"] != state["plan"]["id"]:
                raise ValueError("PLAN_EXECUTION_IDENTITY_MISMATCH")
            if not current["run_id"]:
                state["stop_code"] = "UNCERTAIN_APPROVAL_OR_QUEUE_RECONCILE_WITH_OPERATOR"
                save_session(directory, state)
                return public_session(state)
            state["run_id"] = str(UUID(current["run_id"]))
            state.pop("stop_code", None)
            save_session(directory, state, "QUEUED")
        if state["phase"] in {"QUEUED", "RUNNING"}:
            report = control("GET", base + f"/experiments/{state['run_id']}")
            state["report"] = {
                key: report[key]
                for key in ("id", "status", "reserved_usd", "spent_usd", "requests")
            }
            state["provider_calls"] = None if report["requests"] else 0
            if report["status"] == "COMPLETED":
                if route["stage"] != "training":
                    decisions = report["results"].get("recommendations", [])
                    if (
                        report["results"].get("billing_complete") is not True
                        or len(decisions) != 1
                        or decisions[0].get("quality_gate_passed") is not True
                        or decisions[0].get("cheaper_on_sample") is not True
                        or (
                            route["stage"] == "confirmation"
                            and report["results"].get("lineage", {}).get("fresh_test_claim")
                            is not True
                        )
                    ):
                        state["stop_code"] = "ROUTING_STAGE_QUALITY_COST_OR_FRESHNESS_NOT_PASSED"
                        save_session(directory, state, "STOPPED")
                        return public_session(state)
                route[route["stage"] + "_run_id"] = state["run_id"]
                if route["stage"] == "training":
                    route["fit_request"] = {
                        "request_id": str(uuid4()),
                        "training_run_id": state["run_id"],
                        "purpose": route["purpose"],
                        "options": {"max_routes": 4, "min_leaf_cases": 3, "min_training_groups": 3},
                    }
                    save_session(directory, state, "PREPARING_ROUTING_FIT")
                elif route["stage"] == "validation":
                    route["stage"] = "confirmation"
                    route["stage_request"] = {
                        "request_id": str(uuid4()),
                        "validation_run_id": state["run_id"],
                        "purpose": route["purpose"],
                        "budget": route["budget"],
                    }
                    save_session(directory, state, "PREPARING_ROUTING_STAGE")
                else:
                    save_session(directory, state, "COMPLETED")
                _prepare_next(root, directory, state, control)
            elif report["status"] in {"QUEUED", "RUNNING", "BLOCKED"}:
                save_session(
                    directory, state, "RUNNING" if report["status"] == "RUNNING" else "QUEUED"
                )
            else:
                state["stop_code"] = "ROUTING_STAGE_FAILED_NO_BILLABLE_RETRY"
                save_session(directory, state, "STOPPED")
        return public_session(state)


def proposal(root, control, *, purpose):
    from pydantic import TypeAdapter

    from cutback.agent import load_session, save_session, workspace
    from cutback.core.models import Identifier
    from cutback.routing_changes import RoutingSourceProposal, prepare_patch

    TypeAdapter(Identifier).validate_python(purpose)
    root, directory = workspace(root)
    with workspace_lock(directory):
        state = load_session(root)
        if state["endpoint"] != control.endpoint:
            raise ValueError("COLLECTOR_DESTINATION_MISMATCH")
        if (
            state["phase"] not in {"COMPLETED", "PROPOSAL_READY"}
            or state["routing"]["stage"] != "confirmation"
        ):
            raise ValueError("ROUTING_COMPLETED_CONFIRMATION_REQUIRED")
        selected_source(root, state, state["selected_workflow"])
        source = control(
            "POST",
            f"/v1/projects/{state['project_id']}/routing/fits/{state['routing']['fit_id']}/source-proposal",
            {"purpose": purpose, "confirmation_run_id": state["run_id"]},
        )
        result = prepare_patch(root, RoutingSourceProposal.model_validate(source))
        state["proposal"] = {
            "run_id": state["run_id"],
            "proposed_sha256": result["proposed_sha256"],
        }
        save_session(directory, state, "PROPOSAL_READY")
        return result
