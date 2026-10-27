"""An agent's durable preparation job. Source analysis is offline and metadata-only."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from cutback.core.inventory import workflow_identity
from cutback.core.request_profiles import fingerprint, prepare_profile
from cutback.core.workspace import private_directory, read_private, workspace_lock, write_private


def now():
    return datetime.now(UTC).isoformat()


def workspace(root: Path):
    root = root.resolve(strict=True)
    directory = private_directory(root, ".cutback/agent")
    ignored = root / ".cutback/.gitignore"
    if ignored.is_symlink():
        raise ValueError("UNSAFE_WORKSPACE_IGNORE")
    if not ignored.exists():
        ignored.write_text("*\n", encoding="utf-8")
    return root, directory


def load_session(root: Path):
    root, directory = workspace(root)
    state = read_private(directory / "session.json")
    if state["root_fingerprint"] != fingerprint(str(root)):
        raise ValueError("WORKFLOW_ROOT_CHANGED")
    return state


def save_session(directory, state, phase=None, code=None):
    if phase:
        state["phase"] = phase
        state["checkpoints"].append({"phase": phase, "code": code or phase, "at": now()})
        state["checkpoints"] = state["checkpoints"][-100:]
    state["updated_at"] = now()
    state["revision"] += 1
    write_private(directory / "session.json", state)
    return state


def next_action(state):
    phase = state["phase"]
    if phase in {"ANALYZED", "SOURCE_CHANGED"}:
        return {
            "code": "REVIEW_DATA_RIGHTS_AND_PREPARE",
            "tool": "cutback workflow prepare",
            "requires": [
                "permitted_example_paths",
                "rights_basis_and_reference",
                "approved_collector_destination",
                "quality_scope_if_known",
                "budget_ceiling",
            ],
            "effects": [
                "read_permitted_examples",
                "upload_to_approved_encrypted_store",
                "freeze_plan",
            ],
            "provider_calls": 0,
        }
    if phase == "PLAN_READY":
        return {
            "code": "REVIEW_EXACT_BUDGET",
            "tool": "cutback workflow continue",
            "requires": ["exact_plan_hash", "budget_approval_reference"],
            "effects": ["one_bounded_run"],
            "plan": state.get("plan"),
        }
    if phase in {"QUEUED", "RUNNING"}:
        return {
            "code": "POLL_SAME_RUN",
            "tool": "cutback workflow continue",
            "requires": [],
            "effects": ["read_existing_run"],
            "requeue": False,
        }
    if phase in {"COMPLETED", "PROPOSAL_READY"}:
        if state.get("search_mode") == "model_and_output_cap" and not state.get("confirmation"):
            return {
                "code": "SELECT_CANDIDATE_AND_FREEZE_CONFIRMATION",
                "tool": "cutback workflow confirm",
                "requires": ["candidate_key", "confirmation_budget", "purpose_reference"],
                "effects": ["freeze_single_candidate_for_untouched_test_groups"],
                "provider_calls": 0,
                "new_exact_approval_required": True,
            }
        return {
            "code": "REVIEW_EVIDENCE_AND_CHANGE",
            "tool": "cutback workflow proposal",
            "requires": ["purpose_reference"],
            "effects": ["read_authorized_encrypted_proposal"],
            "deployment": "separate_approval_required",
        }
    return {
        "code": state.get("stop_code", "INSPECT_WORKFLOW_STATUS"),
        "tool": "cutback workflow status",
        "requires": [],
        "provider_calls": 0,
        "automatic_billable_retry": False,
    }


def public_session(state):
    return {
        **state,
        "next_action": next_action(state),
        "content": "metadata_only",
        "historical_spend_usd": None,
        "production_deployed": False,
    }


def analyze(root: Path, *, workflow: str | None = None, new_session=False):
    from cutback.evals.datasets import discover_fixtures
    from cutback.optimizer import static_opportunities
    from cutback.scanner import scan

    root, directory = workspace(root)
    with workspace_lock(directory):
        inventory = scan(root)
        profiles, unresolved, entrypoints = [], [], {}
        for site in inventory["call_sites"][:200]:
            entries = site["entry_points"] or [
                {"file": site["source_file"], "symbol": site["source_symbol"]}
            ]
            for entry in entries:
                identity, _ = workflow_identity(entry["file"], entry["symbol"])
                if workflow and identity != workflow:
                    continue
                try:
                    profile = prepare_profile(
                        root, site, workflow_id=identity, commit=inventory["project"].get("commit")
                    )
                    profiles.append(profile.public())
                except (ValueError, OSError, SyntaxError, TypeError, RecursionError):
                    unresolved.append(
                        {
                            "call_site_id": site["call_site_id"],
                            "workflow_id": identity,
                            "source_file": site["source_file"],
                            "reason": "REQUEST_REQUIRES_INSPECTED_ADAPTER_OR_EXPLICIT_BOUND",
                        }
                    )
                entrypoints[entry["file"]] = entry
        fixtures = discover_fixtures(root)
        for fixture in fixtures:
            path = root / fixture["path"]
            fixture["identity"] = {"size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}
        signature = fingerprint(
            {
                "profiles": profiles,
                "unresolved": unresolved,
                "fixtures": fixtures,
                "inventory": inventory,
            }
        )
        prior = (
            read_private(directory / "session.json")
            if (directory / "session.json").exists()
            else None
        )
        previous_id = None
        if prior and (new_session or prior["analysis_sha256"] != signature):
            active = prior["phase"] in {"APPROVING", "QUEUEING", "QUEUED", "RUNNING"}
            if active:
                if new_session:
                    raise ValueError("ACTIVE_WORKFLOW_MUST_BE_RECONCILED_OR_CANCELLED")
                prior["source_status"] = "CHANGED_RESCAN_AFTER_RUN"
                prior["observed_analysis_sha256"] = signature
                save_session(directory, prior)
                return public_session(prior)
            previous_id = str(UUID(prior["id"]))
            archive = directory / f"session-{previous_id}.json"
            if not archive.exists():
                write_private(archive, prior, exclusive=True)
            prior = None
        if prior and prior["analysis_sha256"] == signature:
            return public_session(prior)
        state = {
            "version": "agent-workflow-v1",
            "id": str(uuid4()) if not prior else prior["id"],
            "root_fingerprint": fingerprint(str(root)),
            "analysis_sha256": signature,
            "revision": prior["revision"] if prior else 0,
            "created_at": prior["created_at"] if prior else now(),
            "phase": "ANALYZED",
            "checkpoints": prior["checkpoints"] if prior else [],
            "profiles": profiles,
            "unresolved": unresolved,
            "fixtures": fixtures,
            "entrypoints": list(entrypoints.values()),
            "opportunities": static_opportunities(inventory)["opportunities"],
            "decisions_required": [],
            "provider_calls": 0,
        }
        state["strategies"] = strategy_assessments(profiles)
        if previous_id:
            state["previous_session_id"] = previous_id
        if not profiles:
            state["decisions_required"].append("request_contract_qualification")
        if not fixtures:
            state["decisions_required"].append("permitted_examples_or_observation_scope")
        inventory["request_profiles"] = [p["binding"] for p in profiles]
        write_private(directory / "inventory.json", inventory)
        prepare_instrumentation(root, directory, state)
        save_session(
            directory, state, "SOURCE_CHANGED" if previous_id and not new_session else "ANALYZED"
        )
        return public_session(state)


def prepare_instrumentation(root, directory, state):
    from cutback.core.instrumentation_plan import instrument_plan

    changes = []
    for entry in state["entrypoints"][:30]:
        try:
            proposal = instrument_plan(root, Path(entry["file"]))
            changes.append(
                {
                    "source_file": proposal["source_file"],
                    "before_sha256": proposal["before_sha256"],
                    "status": "PLAN_ONLY",
                    "lifecycle": proposal["lifecycle"],
                }
            )
        except (ValueError, OSError, SyntaxError):
            changes.append({"source_file": entry["file"], "status": "INSPECT_EXISTING_STARTUP"})
    state["instrumentation"] = {
        "proposals": changes,
        "applied": False,
        "next_tool": "cutback instrument --plan --entrypoint SOURCE --json",
    }
    state["collector"] = {
        "status": "OPTIONAL_OWNED_LOCAL_STACK",
        "next_tool": "cutback collector up --target TARGET",
        "corporate_account_required": False,
    }


def strategy_assessments(profiles):
    from cutback.evals.planning import VALIDATED_MODELS

    return [
        {
            "profile_sha256": p["binding"]["profile_sha256"],
            "model_swap": {
                "status": "prepare_qualified_same_provider_candidates",
                "qualified_models": list(VALIDATED_MODELS.get(p["provider"], ())),
            },
            "prompt_change": {"status": "agent_must_prepare_and_review_task_preserving_variant"},
            "output_cap": {
                "status": "measure_truncation_before_changing",
                "current": p["max_output_tokens"],
            },
            "routing": {"status": "requires_independent_routing_criteria"},
            "cache": {"status": "requires_repeat_and_privacy_evidence"},
            "distillation": {"status": "requires_approved_training_data_compute_and_quality"},
        }
        for p in profiles
    ]


def selected_profile(root, state, workflow=None):
    from cutback.scanner import scan

    choices = [
        p for p in state["profiles"] if not workflow or p["binding"]["workflow_id"] == workflow
    ]
    if len(choices) != 1:
        raise ValueError("SELECT_ONE_QUALIFIED_WORKFLOW")
    binding = choices[0]["binding"]
    inventory = scan(root)
    site = next(
        (s for s in inventory["call_sites"] if s["call_site_id"] == binding["call_site_id"]), None
    )
    if not site:
        raise ValueError("SOURCE_CHANGED_REANALYZE")
    profile = prepare_profile(
        root, site, workflow_id=binding["workflow_id"], commit=inventory["project"].get("commit")
    )
    if profile.binding.model_dump(mode="json") != binding:
        raise ValueError("SOURCE_CHANGED_REANALYZE")
    return profile


def permitted_file(root, relative):
    from cutback.core.request_profiles import safe_relative

    safe_relative(relative)
    path = root / relative
    if (
        path.is_symlink()
        or not path.resolve().is_relative_to(root)
        or any(p.is_symlink() for p in path.parents if p.is_relative_to(root))
        or any(
            p.lower() in {"secrets", "credentials", ".aws", ".ssh"} for p in Path(relative).parts
        )
    ):
        raise ValueError("EXAMPLE_PATH_UNSAFE")
    return path


def search_candidates(baseline):
    from cutback.evals.search import candidate_portfolio

    return candidate_portfolio(baseline)


def prepare(
    root,
    control,
    project,
    *,
    files,
    rights_basis,
    rights_ref,
    max_usd,
    max_requests=100,
    workflow=None,
    minimum=None,
    independent_labels=False,
    representative=False,
    mapping=None,
    search=False,
):
    """Read only explicitly permitted files; upload to the chosen encrypted collector; no calls."""
    from datetime import timedelta

    from cutback.core.models import Identifier
    from cutback.evals.datasets import CapturePolicy, DatasetImport, prepare_dataset
    from cutback.evals.experiments import CriterionSpec, ExperimentPlan, ModelSpec, plan_summary
    from cutback.evals.metrics import MetricSpec
    from cutback.evals.planning import draft_plan
    from cutback.evals.records import read_records

    root, directory = workspace(root)
    from uuid import UUID

    project = str(UUID(str(project)))
    with workspace_lock(directory):
        state = load_session(root)
        if state["phase"] not in {"ANALYZED", "SOURCE_CHANGED", "PLAN_READY"}:
            raise ValueError("WORKFLOW_PREPARATION_ALREADY_STARTED_INSPECT_STATUS")
        profile = selected_profile(root, state, workflow)
        if not files or len(files) > 20:
            raise ValueError("ONE_TO_TWENTY_PERMITTED_FILES_REQUIRED")
        rights = CapturePolicy(
            enabled=True,
            approval_ref=rights_ref,
            rights_basis=rights_basis,
            environments=["staging"],
            workflows=[profile.binding.workflow_id],
            allowed_providers=[profile.provider],
            expires_at=datetime.now(UTC) + timedelta(days=1),
            retention_days=1,
            sample_rate=1,
            max_samples=10000,
            max_payload_bytes=512000,
        )
        cases = []
        identities = []
        for name in files:
            path = permitted_file(root, name)
            cases.extend(read_records(path, mapping=mapping))
            identities.append(
                {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            )
        from pydantic import TypeAdapter

        TypeAdapter(Identifier).validate_python(rights_ref)
        body = DatasetImport(
            workflow=profile.binding.workflow_id,
            policy_id=str(uuid4()),
            source_type="synthetic_fixture"
            if rights_basis == "synthetic"
            else "application_records",
            provenance_ref=rights_ref,
            independent_labels=independent_labels,
            representative=representative,
            cases=cases,
        )
        prepared = prepare_dataset(body)
        metadata = {
            "workflow": body.workflow,
            "hash": prepared["immutable_hash"],
            "provenance": {
                "independent_labels": independent_labels,
                "task_inference": prepared["task_inference"],
                "max_input_bytes": max(len(c.input.encode()) for c in cases),
            },
        }
        baseline = ModelSpec.model_validate(profile.baseline())
        # Account for the rendered native request, including static message/schema content.
        import json

        overhead = max(
            len(json.dumps(profile.request.render(c.input), ensure_ascii=False).encode())
            for c in cases
        )
        bound = (
            overhead
            + len(profile.instruction.encode())
            + len(json.dumps(profile.request.options).encode())
            + 4096
        )
        if bound > 100000:
            raise ValueError("REQUEST_EXCEEDS_QUALIFIED_INPUT_BOUND")
        baseline = baseline.model_copy(update={"max_input_tokens": max(8192, bound)})
        spec = draft_plan(
            metadata,
            baseline,
            minimum=minimum,
            max_usd=max_usd,
            max_requests=max_requests,
            candidates=search_candidates(baseline) if search else None,
        )
        criteria = []
        if (
            spec.metric.kind == "field_f1"
            and minimum is not None
            and all(isinstance(c.gold, dict) for c in cases)
        ):
            fields = set.intersection(*(set(c.gold) for c in cases if isinstance(c.gold, dict)))
            for field in sorted(fields)[:8]:
                if all(
                    isinstance(c.gold, dict) and isinstance(c.gold.get(field), str) for c in cases
                ):
                    criteria.append(
                        CriterionSpec(
                            key=field,
                            metric=MetricSpec(
                                kind="exact_label", output_field=field, minimum=minimum
                            ),
                        )
                    )
        spec = ExperimentPlan.model_validate(
            {
                **spec.model_dump(mode="json"),
                "source_binding": profile.binding.model_dump(mode="json"),
                "criteria": [c.model_dump(mode="json") for c in criteria],
                "split": "validation" if search else "test",
            }
        )
        summary = plan_summary(spec, prepared)
        if not summary["fits_budget"]:
            raise ValueError("PROPOSED_PLAN_EXCEEDS_BUDGET_OR_HAS_NO_HELDOUT_CASES")
        identity = fingerprint(
            {
                "source": profile.binding.profile_sha256,
                "examples": identities,
                "rights": rights_ref,
                "rights_basis": rights_basis,
                "project": project,
                "endpoint": control.endpoint,
                "spec": {**spec.model_dump(mode="json"), "dataset_hash": None},
            }
        )
        if state["phase"] == "PLAN_READY":
            if state["preparation_sha256"] != identity:
                raise ValueError("FROZEN_PLAN_CHANGED_START_NEW_SESSION")
            return public_session(state)
        base = f"/v1/projects/{project}"
        state.update(
            project_id=project,
            endpoint=control.endpoint,
            selected_workflow=body.workflow,
            preparation_sha256=identity,
            search_mode="model_and_output_cap" if search else "qualified_model_swap",
            permitted_files=identities,
            rights_ref=rights_ref,
            quality_scope="heldout_independent_labels"
            if spec.metric.kind in {"field_f1", "exact_label"}
            else "technical_only_not_verified",
        )
        save_session(directory, state, "PREPARING")
        inventory = read_private(directory / "inventory.json")
        control("POST", base + "/scans", inventory)
        policy = control("POST", base + "/capture-policies", rights.model_dump(mode="json"))
        state["policy_id"] = policy["id"]
        save_session(directory, state)
        body = body.model_copy(update={"policy_id": policy["id"]})
        dataset = control("POST", base + "/datasets", body.model_dump(mode="json"))
        state["dataset_id"] = dataset["id"]
        state["dataset_split_counts"] = dataset["provenance"]["split_counts"]
        save_session(directory, state)
        spec = ExperimentPlan.model_validate(
            {**spec.model_dump(mode="json"), "dataset_hash": dataset["immutable_hash"]}
        )
        plan = control(
            "POST",
            base + "/experiments/plans",
            {"dataset_id": dataset["id"], "specification": spec.model_dump(mode="json")},
        )
        state["plan"] = plan
        save_session(directory, state, "PLAN_READY")
        return public_session(state)


def advance(root, control, *, plan_hash=None, approval_ref=None):
    """One exact approval, one run, subsequent invocations only poll that run."""
    root, directory = workspace(root)
    with workspace_lock(directory):
        state = load_session(root)
        if state.get("endpoint") != control.endpoint:
            raise ValueError("COLLECTOR_DESTINATION_MISMATCH")
        base = f"/v1/projects/{state['project_id']}"
        if state["phase"] == "PLAN_READY":
            selected_profile(root, state, state["selected_workflow"])
            if plan_hash != state["plan"]["plan_hash"] or not approval_ref:
                raise ValueError("EXACT_PLAN_HASH_AND_APPROVAL_REFERENCE_REQUIRED")
            from pydantic import TypeAdapter

            from cutback.core.models import Identifier

            TypeAdapter(Identifier).validate_python(approval_ref)
            state["provider_calls"] = None
            # A lost response cannot cause a new approval/run on resume.
            save_session(directory, state, "APPROVING")
            control(
                "POST",
                base + f"/experiments/plans/{state['plan']['id']}/approve",
                {"plan_hash": plan_hash, "approval_ref": approval_ref},
            )
            save_session(directory, state, "QUEUEING")
            queued = control("POST", base + "/experiments", {"plan_id": state["plan"]["id"]})
            state["run_id"] = queued["id"]
            save_session(directory, state, "QUEUED")
        if state["phase"] in {"APPROVING", "QUEUEING"}:
            # Reconcile only by existing immutable plan/run. Never send another POST.
            matches: list[dict] = []
            for offset in range(0, 1000, 100):
                page = control("GET", base + f"/experiments?limit=100&offset={offset}")
                matches.extend(r for r in page if r["plan_id"] == state["plan"]["id"])
                if len(page) < 100:
                    break
            if len(matches) != 1:
                state["stop_code"] = "UNCERTAIN_APPROVAL_OR_QUEUE_RECONCILE_WITH_OPERATOR"
                save_session(directory, state)
                return public_session(state)
            state["run_id"] = matches[0]["id"]
            save_session(directory, state, "QUEUED")
        if state["phase"] in {"QUEUED", "RUNNING", "COMPLETED", "PROPOSAL_READY"}:
            report = control("GET", base + f"/experiments/{state['run_id']}")
            state["report"] = {
                key: report[key]
                for key in ("id", "status", "reserved_usd", "spent_usd", "requests")
            }
            # Reservations are known; whether each request reached a paid provider may be unknown.
            state["provider_calls"] = None if report["requests"] else 0
            state["decisions"] = report["results"].get("recommendations", [])
            from cutback.evals.search import sample_frontier

            state["search_results"] = sample_frontier(report["results"])
            phase = report["status"]
            save_session(
                directory,
                state,
                "COMPLETED"
                if phase == "COMPLETED"
                else "RUNNING"
                if phase == "RUNNING"
                else "QUEUED"
                if phase in {"QUEUED", "BLOCKED"}
                else "STOPPED",
            )
            if phase == "COMPLETED":
                state["next_review"] = (
                    "cutback workflow confirm --candidate KEY --max-usd CEILING "
                    "--purpose-reference REVIEW"
                    if state.get("search_mode") == "model_and_output_cap"
                    and not state.get("confirmation")
                    else "cutback workflow proposal --purpose-reference REVIEW"
                )
                save_session(directory, state)
        return public_session(state)


def gate(root, control, *, candidate=None, sample_only=False):
    """Read current canonical evidence and current source; no inference or content reads."""
    from cutback.evals.search import regression_gate

    root, directory = workspace(root)
    with workspace_lock(directory):
        state = load_session(root)
        if state["phase"] not in {"COMPLETED", "PROPOSAL_READY"}:
            raise ValueError("COMPLETED_RUN_REQUIRED")
        if state["endpoint"] != control.endpoint:
            raise ValueError("COLLECTOR_DESTINATION_MISMATCH")
        selected_profile(root, state, state["selected_workflow"])
        report = control("GET", f"/v1/projects/{state['project_id']}/experiments/{state['run_id']}")
        return regression_gate(
            report["results"],
            expected_plan_hash=state["plan"]["plan_hash"],
            candidate=candidate,
            sample_only=sample_only,
        )


def confirm(root, control, *, candidate, max_usd, max_requests=100, purpose):
    """Freeze one validation winner for a separately approved untouched test run."""
    from cutback.evals.experiments import Budget, ExperimentPlan, plan_summary

    root, directory = workspace(root)
    with workspace_lock(directory):
        state = load_session(root)
        if state["endpoint"] != control.endpoint:
            raise ValueError("COLLECTOR_DESTINATION_MISMATCH")
        if (
            state["phase"] != "COMPLETED"
            or state.get("confirmation")
            or state.get("search_mode") != "model_and_output_cap"
        ):
            raise ValueError("COMPLETED_SEARCH_REQUIRED")
        selected_profile(root, state, state["selected_workflow"])
        base = f"/v1/projects/{state['project_id']}"
        content = control(
            "POST", base + f"/experiments/plans/{state['plan']['id']}/content", {"purpose": purpose}
        )
        spec = content["specification"]
        matches = [m for m in spec["candidates"] if m["key"] == candidate]
        if len(matches) != 1 or spec["split"] != "validation":
            raise ValueError("SELECT_ONE_FROZEN_SEARCH_CANDIDATE")
        decision: dict = next(
            (r for r in state.get("decisions", []) if r["candidate"]["key"] == candidate), {}
        )
        if not decision.get("quality_gate_passed") or not decision.get("cheaper_on_sample"):
            raise ValueError("SELECTED_SEARCH_CANDIDATE_DID_NOT_PASS_GATES")
        spec.update(
            candidates=matches,
            split="test",
            predecessor_run_id=state["run_id"],
            budget=Budget(max_usd=max_usd, max_requests=max_requests).model_dump(mode="json"),
        )
        spec = ExperimentPlan.model_validate(spec)
        summary = plan_summary(
            spec,
            {
                "immutable_hash": spec.dataset_hash,
                "items": [{"split": "test"}] * state["dataset_split_counts"].get("test", 0),
            },
        )
        if not summary["fits_budget"]:
            raise ValueError("CONFIRMATION_EXCEEDS_BUDGET_OR_HAS_NO_TEST_GROUPS")
        state["confirmation"] = {
            "search_run_id": state["run_id"],
            "search_plan_id": state["plan"]["id"],
            "selected_candidate": candidate,
        }
        # A lost nonbillable preparation reply stops visibly; it never requeues a run.
        save_session(directory, state, "PREPARING_CONFIRMATION")
        plan = control(
            "POST",
            base + "/experiments/plans",
            {
                "dataset_id": state["dataset_id"],
                "specification": spec.model_dump(mode="json"),
            },
        )
        state["plan"] = plan
        for field in ("run_id", "report", "decisions", "search_results", "proposal"):
            state.pop(field, None)
        save_session(directory, state, "PLAN_READY")
        return public_session(state)


def proposal(root, control, *, purpose, candidate=None):
    """Regenerate a concrete local diff in memory; no acceptance, upload or apply implied."""
    from cutback.changes import model_patch

    root, directory = workspace(root)
    with workspace_lock(directory):
        state = load_session(root)
        if state["phase"] not in {"COMPLETED", "PROPOSAL_READY"}:
            raise ValueError("COMPLETED_RUN_REQUIRED")
        profile = selected_profile(root, state, state["selected_workflow"])
        base = f"/v1/projects/{state['project_id']}"
        if state["endpoint"] != control.endpoint:
            raise ValueError("COLLECTOR_DESTINATION_MISMATCH")
        # Content purpose and current rights are checked and audited by the collector.
        content = control(
            "POST", base + f"/experiments/plans/{state['plan']['id']}/content", {"purpose": purpose}
        )
        if content["specification"]["split"] != "test":
            raise ValueError("SEARCH_REQUIRES_INDEPENDENT_CONFIRMATION_BEFORE_PATCH")
        records = control("GET", base + f"/recommendations?run_id={state['run_id']}")
        choices = [
            r
            for r in records
            if r["run_id"] == state["run_id"]
            and r["evidence"].get("quality_gate_passed")
            and r["evidence"].get("cheaper_on_sample")
            and (candidate is None or r["evidence"]["candidate"]["key"] == candidate)
        ]
        if not choices:
            raise ValueError("NO_SOURCE_PATCH_CANDIDATE_PASSED_GATES")
        from decimal import Decimal

        choices.sort(
            key=lambda r: (
                -Decimal(r["evidence"]["sample_modeled_cost_difference_usd"]),
                r["evidence"]["candidate"]["key"],
            )
        )
        artifact = model_patch(
            root,
            read_private(directory / "inventory.json"),
            profile.binding.call_site_id,
            choices[0],
            expected_source_sha256=profile.binding.dependencies[
                profile.binding.model_binding["file"]
            ],
            proposal_only=True,
        )
        state["proposal"] = {
            key: artifact[key]
            for key in ("diff_sha256", "source_file", "recommendation_id", "run_id")
        }
        save_session(directory, state, "PROPOSAL_READY")
        return artifact
