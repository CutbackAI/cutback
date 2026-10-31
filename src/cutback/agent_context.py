"""Resumable preparation of an explicitly qualified joint model/context experiment."""

import hashlib
from uuid import UUID, uuid4

from cutback.core.request_profiles import fingerprint


def selected_source(root, state, workflow=None):
    from cutback.joint_context_changes import inspect_producer

    choices = [
        value
        for value in state.get("context_producers", [])
        if not workflow or value["workflow_id"] == workflow
    ]
    if len(choices) != 1:
        raise ValueError("SELECT_ONE_QUALIFIED_CONTEXT_WORKFLOW")
    value = choices[0]
    _, binding, producer = inspect_producer(root, value["producer_binding"]["file"])
    if (
        binding.model_dump(mode="json") != value["configuration_binding"]
        or producer.model_dump(mode="json") != value["producer_binding"]
    ):
        raise ValueError("CONTEXT_SOURCE_CHANGED_REANALYZE")
    return value


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
):
    from cutback.agent import load_session, permitted_file, public_session, save_session, workspace
    from cutback.core.compression import CompressionConfigurationBinding, ContextInput
    from cutback.core.context_producer import ContextProducerBinding
    from cutback.core.workspace import read_private, workspace_lock
    from cutback.evals.datasets import CapturePolicy, DatasetImport, prepare_dataset
    from cutback.evals.experiments import Budget, ExperimentPlan, ModelSpec, plan_summary
    from cutback.evals.joint_context_planning import draft_joint_plan
    from cutback.evals.metrics import MetricSpec
    from cutback.evals.records import read_records

    root, directory = workspace(root)
    project = str(UUID(str(project)))
    with workspace_lock(directory):
        state = load_session(root)
        if state["phase"] not in {"ANALYZED", "SOURCE_CHANGED", "PLAN_READY", "PREPARING"}:
            raise ValueError("WORKFLOW_PREPARATION_ALREADY_STARTED_INSPECT_STATUS")
        source = selected_source(root, state, workflow)
        if not independent_labels or minimum is None:
            raise ValueError("JOINT_SEARCH_REQUIRES_EXPLICIT_INDEPENDENT_QUALITY_CRITERIA")
        if not files or len(files) > 20:
            raise ValueError("ONE_TO_TWENTY_PERMITTED_FILES_REQUIRED")
        from cutback.agent_preparation import begin, expiry

        preparation = begin(directory, state, "joint_model_context")
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
        cases, identities = [], []
        for name in files:
            path = permitted_file(root, name)
            if path.stat().st_size > 1048576:
                raise ValueError("EXAMPLE_FILE_UNSAFE_OR_EXCEEDS_ONE_MIB")
            raw = path.read_bytes()
            cases.extend(read_records(path, mapping=mapping))
            if path.read_bytes() != raw:
                raise ValueError("JOINT_EXAMPLES_CHANGED_DURING_PREPARATION")
            identities.append({"path": name, "sha256": hashlib.sha256(raw).hexdigest()})
        for case in cases:
            ContextInput.parse(case.input)
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
        binding = CompressionConfigurationBinding.model_validate(source["configuration_binding"])
        producer = ContextProducerBinding.model_validate(source["producer_binding"])
        metric = MetricSpec(
            kind="exact_label",
            minimum=minimum,
            min_test_samples=min_test_samples,
            min_test_groups=min_test_samples,
        )
        budget = Budget(max_usd=max_usd, max_requests=max_requests)
        intent = fingerprint(
            {
                "source": source,
                "examples": identities,
                "rights": rights_ref,
                "rights_basis": rights_basis,
                "project": project,
                "endpoint": control.endpoint,
                "metric": metric.model_dump(mode="json"),
                "budget": budget.model_dump(mode="json"),
                "assumed_usd_per_cpu_hour": assumed_usd_per_cpu_hour,
                "representative": representative,
                "retain_case_outputs": retain_case_outputs,
            }
        )
        from cutback.agent_preparation import bind

        intent = bind(intent, preparation, body, policy)
        if state["phase"] in {"PLAN_READY", "PREPARING"}:
            if (
                state.get("search_mode") != "joint_model_context"
                or state.get("preparation_sha256") != intent
            ):
                raise ValueError("FROZEN_PLAN_CHANGED_START_NEW_SESSION")
            if state["phase"] == "PLAN_READY":
                return public_session(state)
        baseline = ModelSpec(
            key="baseline",
            provider="openai",
            model=producer.model,
            max_output_tokens=producer.max_output_tokens,
            max_input_tokens=min(100000, (1024 + binding.settings.max_context_bytes) * 6 + 8192),
        )
        specification = draft_joint_plan(
            {
                "workflow": body.workflow,
                "hash": prepared["immutable_hash"],
                "provenance": {"independent_labels": True},
            },
            baseline,
            binding.settings,
            metric=metric,
            budget=budget,
            assumed_usd_per_cpu_hour=assumed_usd_per_cpu_hour,
            retain_case_outputs=retain_case_outputs,
            configuration_binding=binding,
            producer_binding=producer,
        )
        if preparation:
            specification = specification.model_copy(
                update={"content_nonce": UUID(preparation["content_nonce"])}
            )
        if not plan_summary(specification, prepared)["fits_budget"]:
            raise ValueError("PROPOSED_JOINT_PLAN_EXCEEDS_BUDGET_OR_HAS_NO_VALIDATION_CASES")
        # Recheck clean source after reading examples and before any upload.
        selected_source(root, state, source["workflow_id"])
        state.update(
            project_id=project,
            endpoint=control.endpoint,
            selected_workflow=body.workflow,
            preparation_sha256=intent,
            search_mode="joint_model_context",
            permitted_files=identities,
            rights_ref=rights_ref,
            quality_scope="heldout_independent_labels",
        )
        state["preparation_command"] = {
            "files": files,
            "rights_basis": rights_basis,
            "rights_ref": rights_ref,
            "max_usd": str(max_usd),
            "max_requests": max_requests,
            "workflow": body.workflow,
            "minimum": minimum,
            "independent_labels": independent_labels,
            "representative": representative,
            "retain_case_outputs": retain_case_outputs,
            "mapping": mapping,
            "joint_context": True,
            "assumed_usd_per_cpu_hour": None
            if assumed_usd_per_cpu_hour is None
            else str(assumed_usd_per_cpu_hour),
            "min_test_samples": min_test_samples,
        }
        save_session(directory, state, "PREPARING")
        from cutback import agent_preparation as setup

        setup.require_protocol(control)
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
        state["dataset_id"] = dataset["id"]
        state["dataset_split_counts"] = dataset["provenance"]["split_counts"]
        save_session(directory, state)
        specification = ExperimentPlan.model_validate(
            {**specification.model_dump(mode="json"), "dataset_hash": dataset["immutable_hash"]}
        )
        plan = setup.plan(
            control,
            base,
            dataset["id"],
            specification,
            preparation,
            state=state,
            directory=directory,
        )
        state["plan"] = plan
        save_session(directory, state, "PLAN_READY")
        return public_session(state)
