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
    if phase == "PREPARING" and state.get("preparation_intent"):
        if state.get("preparation_conflict"):
            return {
                "code": "RECONCILE_PREPARATION_IDENTITY_WITH_OPERATOR",
                "tool": "cutback workflow status",
                "requires": ["explicit_server_identity_or_content_reconciliation"],
                "provider_calls": 0,
                "automatic_billable_retry": False,
            }
        return {
            "code": "RESUME_SAME_NONBILLABLE_PREPARATION",
            "tool": "cutback workflow resume-preparation",
            "requires": [],
            "provider_calls": 0,
            "automatic_billable_retry": False,
        }
    if state.get("search_mode") == "source_routing":
        from cutback.agent_routing import next_action as routing_action

        return routing_action(state)
    if phase == "PREPARING_CONFIRMATION" and state.get("search_mode") == "reviewed_criteria":
        return {
            "code": "RESUME_SAME_CONFIRMATION_PREPARATION",
            "tool": "cutback workflow confirm",
            "requires": ["same_selected_candidate", "same_budget", "purpose_reference"],
            "effects": ["reconcile_nonbillable_preparation"],
            "provider_calls": 0,
        }
    if phase == "CRITERIA_PLAN_READY":
        return {
            "code": "REVIEW_EXACT_CRITERIA_GENERATION_BUDGET",
            "tool": "cutback workflow continue",
            "requires": ["exact_generation_plan_hash", "generation_budget_approval_reference"],
            "effects": ["one_training_only_generation_call"],
            "plan": state.get("criteria", {}).get("generation"),
            "automatic_billable_retry": False,
        }
    if phase in {"APPROVING_CRITERIA", "QUEUEING_CRITERIA", "CRITERIA_QUEUED", "CRITERIA_RUNNING"}:
        return {
            "code": "RESUME_SAME_CRITERIA_GENERATION",
            "tool": "cutback workflow continue",
            "requires": [],
            "effects": ["reconcile_same_approved_parent"],
            "automatic_billable_retry": False,
        }
    if phase == "CRITERIA_REVIEW_REQUIRED":
        return {
            "code": "READ_AND_REVIEW_FROZEN_CRITERIA",
            "tool": "cutback workflow criteria-read",
            "requires": [
                "purpose_reference",
                "protected_output_file",
                "explicit_criteria_decision",
            ],
            "effects": ["read_authorized_private_checks"],
            "provider_calls": 0,
        }
    if phase in {"PREPARING_CRITERIA", "REVIEWING_CRITERIA", "PREPARING_REVIEWED_VALIDATION"}:
        return {
            "code": "RESUME_SAME_CRITERIA_INTENT",
            "tool": "cutback workflow criteria-prepare"
            if phase == "PREPARING_CRITERIA"
            else "cutback workflow criteria-review",
            "requires": ["same_original_request_or_decision", "purpose_reference"],
            "effects": ["reconcile_nonbillable_preparation"],
            "provider_calls": 0,
        }
    if phase in {"ANALYZED", "SOURCE_CHANGED"}:
        if state.get("routing_producers") and not state.get("profiles"):
            return {
                "code": "PREPARE_SOURCE_BOUND_ROUTING",
                "tool": "cutback workflow prepare --learned-routing",
                "requires": [
                    "permitted_labeled_examples",
                    "rights_reference",
                    "approved_collector",
                    "stage_budget_ceiling",
                    "independent_quality_threshold",
                ],
                "provider_calls": 0,
            }
        if state.get("context_producers") and not state.get("profiles"):
            return {
                "code": "REVIEW_DATA_RIGHTS_AND_PREPARE_JOINT_CONTEXT",
                "tool": "cutback workflow prepare --joint-context",
                "requires": [
                    "permitted_labeled_query_context_examples",
                    "rights_reference",
                    "approved_collector",
                    "budget_ceiling",
                    "declared_cpu_cost_basis",
                ],
                "effects": [
                    "read_permitted_examples",
                    "upload_to_approved_encrypted_store",
                    "freeze_joint_model_context_grid",
                ],
                "provider_calls": 0,
            }
        if not state.get("profiles") and any(
            item.get("diagnostic_code") == "CUSTOM_PROVIDER_DESTINATION_REQUIRES_REVIEW"
            for item in state.get("unresolved", [])
        ):
            return {
                "code": "QUALIFY_CLIENT_DESTINATION",
                "tool": "cutback workflow analyze",
                "requires": ["explicit_supported_client_options_or_inspected_destination_adapter"],
                "effects": ["read_source_metadata"],
                "provider_calls": 0,
            }
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
        if state.get("search_mode") == "campaign_basis":
            return {
                "code": "PREPARE_BOUNDED_CAMPAIGN",
                "tool": "cutback campaign prepare",
                "requires": [
                    "matching_complete_task_cost_snapshot",
                    "conservative_opportunity",
                    "campaign_ceilings",
                ],
                "effects": ["freeze_encrypted_campaign_envelope"],
                "provider_calls": 0,
            }
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
        if state.get("search_mode") in {
            "model_and_output_cap",
            "joint_model_context",
            "reviewed_criteria",
        } and not state.get("confirmation"):
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
        matching = []
        for site in inventory["call_sites"]:
            entries = site["entry_points"] or [
                {"file": site["source_file"], "symbol": site["source_symbol"]}
            ]
            selected = {}
            for entry in entries:
                identity, _ = workflow_identity(entry["file"], entry["symbol"])
                if not workflow or identity == workflow:
                    # A function-root and HTTP-route hint can name the same
                    # entry. Preserve its identity without creating ambiguous
                    # duplicate native profiles for an ordinary web application.
                    if identity not in selected or entry.get("kind") == "http_route":
                        selected[identity] = (entry, identity)
            if selected:
                matching.append((site, list(selected.values())))
        profile_analysis = {
            "detected_call_sites": len(inventory["call_sites"]),
            "matching_call_sites": len(matching),
            "inspected_call_sites": min(len(matching), 200),
            "omitted_call_sites": max(0, len(matching) - 200),
            "call_site_limit": 200,
            "scope": "detected_static_candidates_not_runtime_or_exhaustive_source_coverage",
        }
        for site, entries in matching[:200]:
            for entry, identity in entries:
                try:
                    profile = prepare_profile(
                        root, site, workflow_id=identity, commit=inventory["project"].get("commit")
                    )
                    profiles.append(profile.public())
                except (ValueError, OSError, SyntaxError, TypeError, RecursionError) as exc:
                    issue = {
                        "call_site_id": site["call_site_id"],
                        "workflow_id": identity,
                        "source_file": site["source_file"],
                        "reason": "REQUEST_REQUIRES_INSPECTED_ADAPTER_OR_EXPLICIT_BOUND",
                    }
                    # Never expose arbitrary parser/provider exception text. This
                    # exact fixed code carries no URL, source content or credential.
                    if (
                        type(exc) is ValueError
                        and str(exc) == "CUSTOM_PROVIDER_DESTINATION_REQUIRES_REVIEW"
                    ):
                        issue.update(
                            diagnostic_code="CUSTOM_PROVIDER_DESTINATION_REQUIRES_REVIEW",
                            next_action="Metadata observation can continue; source replay needs "
                            "explicit client options and a qualified provider destination.",
                        )
                    unresolved.append(issue)
                entrypoints[entry["file"]] = entry
        fixtures = discover_fixtures(root)
        context_producers = []
        from cutback.application_checks import snapshot
        from cutback.core.context_producer import qualify
        from cutback.joint_context_changes import inspect_producer

        context_files = []
        try:
            context_files = sorted(name for name in snapshot(root).files if name.endswith(".py"))
        except (ValueError, OSError):
            pass
        context_analysis = {
            "tracked_python_files": len(context_files),
            "inspected_files": min(200, len(context_files)),
            "omitted_files": max(0, len(context_files) - 200),
            "file_limit": 200,
            "qualification": "exact_wrapper_in_clean_git_source_only",
        }
        routing_producers = []
        from cutback.core.routing_producer import qualify as qualify_routing
        from cutback.routing_changes import inspect_configuration

        for name in context_files[:200]:
            identity, _ = workflow_identity(name, "classify")
            if not workflow or identity == workflow:
                try:
                    path = permitted_file(root, name)
                    if path.stat().st_size <= 65536:
                        qualify_routing(path.read_bytes())
                        _, routing_source = inspect_configuration(root, name)
                        routing_producers.append(
                            {
                                "workflow_id": identity,
                                "source": routing_source.model_dump(mode="json"),
                            }
                        )
                except (ValueError, OSError):
                    pass
            identity, _ = workflow_identity(name, "answer")
            if workflow and identity != workflow:
                continue
            try:
                path = permitted_file(root, name)
                if path.stat().st_size > 65536:
                    continue
                qualify(path.read_bytes())
                _, configuration, producer = inspect_producer(root, name)
            except (ValueError, OSError):
                continue
            context_producers.append(
                {
                    "workflow_id": identity,
                    "configuration_binding": configuration.model_dump(mode="json"),
                    "producer_binding": producer.model_dump(mode="json"),
                }
            )
        for fixture in fixtures:
            path = root / fixture["path"]
            fixture["identity"] = {"size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}
        signature = fingerprint(
            {
                "profiles": profiles,
                "unresolved": unresolved,
                "fixtures": fixtures,
                "inventory": inventory,
                "profile_analysis": profile_analysis,
                "context_producers": context_producers,
                "context_profile_analysis": context_analysis,
                "routing_producers": routing_producers,
            }
        )
        prior = (
            read_private(directory / "session.json")
            if (directory / "session.json").exists()
            else None
        )
        previous_id = None
        if prior and (new_session or prior["analysis_sha256"] != signature):
            active = prior["phase"] in {
                "APPROVING",
                "QUEUEING",
                "QUEUED",
                "RUNNING",
                "PREPARING_CRITERIA",
                "CRITERIA_PLAN_READY",
                "APPROVING_CRITERIA",
                "QUEUEING_CRITERIA",
                "CRITERIA_QUEUED",
                "CRITERIA_RUNNING",
                "CRITERIA_REVIEW_REQUIRED",
                "REVIEWING_CRITERIA",
                "PREPARING_REVIEWED_VALIDATION",
                "PREPARING_CONFIRMATION",
            }
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
            "context_producers": context_producers,
            "routing_producers": routing_producers,
            "context_profile_analysis": context_analysis,
            "profile_analysis": profile_analysis,
            "unresolved": unresolved,
            "fixtures": fixtures,
            "entrypoints": list(entrypoints.values()),
            "opportunities": static_opportunities(inventory)["opportunities"],
            "decisions_required": [],
            "provider_calls": 0,
        }
        state["strategies"] = strategy_assessments(profiles)
        from cutback.agent_strategy import preparation_options

        state["preparation_options"] = preparation_options(state)
        if previous_id:
            state["previous_session_id"] = previous_id
        if not profiles and not context_producers and not routing_producers:
            state["decisions_required"].append("request_contract_qualification")
        if not fixtures:
            state["decisions_required"].append("permitted_examples_or_observation_scope")
        if profile_analysis["omitted_call_sites"]:
            state["decisions_required"].append("select_workflow_to_inspect_remaining_call_sites")
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
        "entrypoint_file_limit": 30,
        "processed_entrypoint_files": len(changes),
        "omitted_entrypoint_files": max(0, len(state["entrypoints"]) - 30),
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
    if state.get("search_mode") == "joint_model_context":
        from cutback.agent_context import selected_source

        return selected_source(root, state, workflow)
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


def campaign_basis_candidates(baseline):
    """Prefer a pure model downgrade; retain an honest unchanged control if none exists."""
    from cutback.evals.experiments import upper_cost

    try:
        choices = search_candidates(baseline)
    except ValueError as exc:
        if str(exc) != "NO_QUALIFIED_SEARCH_VARIANT_SUBMIT_REVIEWED_CANDIDATE":
            raise
        return [
            baseline.model_copy(
                update={
                    "key": "campaign-control"
                    if baseline.key != "campaign-control"
                    else "unchanged-control"
                }
            )
        ]
    pure_downgrades = [
        m
        for m in choices
        if m.model != baseline.model and m.max_output_tokens == baseline.max_output_tokens
    ]
    return [min(pure_downgrades or choices, key=upper_cost)]


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
    retain_case_outputs=False,
    mapping=None,
    search=False,
    for_campaign=False,
    campaign_generator_provider=None,
    joint_context=False,
    assumed_usd_per_cpu_hour=None,
    min_test_samples=30,
    criteria_generator_provider=None,
    learned_routing=False,
    fixture_transport=False,
    automatic=False,
):
    """Read only explicitly permitted files; upload to the chosen encrypted collector; no calls."""
    from cutback.core.models import Identifier
    from cutback.evals.datasets import CapturePolicy, DatasetImport, prepare_dataset
    from cutback.evals.experiments import CriterionSpec, ExperimentPlan, ModelSpec, plan_summary
    from cutback.evals.metrics import MetricSpec
    from cutback.evals.planning import draft_plan
    from cutback.evals.records import read_records

    if type(automatic) is not bool:
        raise ValueError("AUTOMATIC_PREPARATION_FLAG_MUST_BE_BOOLEAN")
    if type(min_test_samples) is not int or not 1 <= min_test_samples <= 10000:
        raise ValueError("EVIDENCE_SAMPLE_MINIMUM_ONE_TO_TEN_THOUSAND_REQUIRED")
    if automatic:
        if any(
            (
                search,
                for_campaign,
                joint_context,
                learned_routing,
                campaign_generator_provider is not None,
                criteria_generator_provider is not None,
            )
        ):
            raise ValueError("AUTOMATIC_PREPARATION_CANNOT_OVERRIDE_EXPLICIT_ENGINE")
        from cutback.agent_strategy import freeze_preparation

        selected = freeze_preparation(root, workflow)
        workflow = selected["workflow_id"]
        search = selected["mode"] == "native_search"
        joint_context = selected["mode"] == "joint_model_context"
        learned_routing = selected["mode"] == "source_routing"
    if learned_routing:
        if (
            joint_context
            or search
            or for_campaign
            or campaign_generator_provider
            or criteria_generator_provider
        ):
            raise ValueError("LEARNED_ROUTING_REQUIRES_ITS_OWN_FROZEN_SEARCH")
        from cutback.agent_routing import prepare as prepare_routing

        return prepare_routing(
            root,
            control,
            project,
            files=files,
            rights_basis=rights_basis,
            rights_ref=rights_ref,
            max_usd=max_usd,
            workflow=workflow,
            max_requests=max_requests,
            minimum=minimum,
            independent_labels=independent_labels,
            representative=representative,
            mapping=mapping,
            min_test_samples=min_test_samples,
            fixture_transport=fixture_transport,
        )
    if fixture_transport and (
        rights_basis != "synthetic" or joint_context or for_campaign or criteria_generator_provider
    ):
        raise ValueError("NATIVE_FIXTURE_REQUIRES_SYNTHETIC_NATIVE_SOURCE_SEARCH")
    if joint_context:
        if search or for_campaign or campaign_generator_provider or criteria_generator_provider:
            raise ValueError("JOINT_CONTEXT_REQUIRES_ITS_OWN_FROZEN_SEARCH")
        from cutback.agent_context import prepare as prepare_context

        return prepare_context(
            root,
            control,
            project,
            files=files,
            rights_basis=rights_basis,
            rights_ref=rights_ref,
            max_usd=max_usd,
            workflow=workflow,
            max_requests=max_requests,
            minimum=minimum,
            independent_labels=independent_labels,
            representative=representative,
            retain_case_outputs=retain_case_outputs,
            mapping=mapping,
            assumed_usd_per_cpu_hour=assumed_usd_per_cpu_hour,
            min_test_samples=min_test_samples,
        )
    root, directory = workspace(root)
    from uuid import UUID

    project = str(UUID(str(project)))
    with workspace_lock(directory):
        state = load_session(root)
        if state["phase"] not in {"ANALYZED", "SOURCE_CHANGED", "PLAN_READY", "PREPARING"}:
            raise ValueError("WORKFLOW_PREPARATION_ALREADY_STARTED_INSPECT_STATUS")
        profile = selected_profile(root, state, workflow)
        if type(for_campaign) is not bool or for_campaign and search:
            raise ValueError("CHOOSE_CAMPAIGN_BASIS_OR_STATIC_SEARCH")
        if campaign_generator_provider is not None and (
            not for_campaign or campaign_generator_provider != "openai"
        ):
            raise ValueError("CAMPAIGN_GENERATOR_PROVIDER_NOT_QUALIFIED")
        providers = {profile.provider}
        if criteria_generator_provider is not None:
            if criteria_generator_provider != "openai" or not search or for_campaign:
                raise ValueError("CRITERIA_GENERATOR_VENDOR_REQUIRES_SEPARATE_NATIVE_SEARCH")
            providers.add("openai")
        if for_campaign:
            if profile.request.sdk_surface not in {"responses.create", "messages.create"}:
                raise ValueError("CAMPAIGN_NATIVE_TEXT_SURFACE_NOT_QUALIFIED")
            previously_allowed = (
                state.get("campaign_allowed_providers", [])
                if state["phase"] == "PLAN_READY"
                and state.get("rights_ref") == rights_ref
                and state.get("search_mode") == "campaign_basis"
                else []
            )
            if campaign_generator_provider == "openai" or "openai" in previously_allowed:
                providers.add("openai")
            if "openai" not in providers:
                raise ValueError("EXPLICIT_OPENAI_GENERATOR_VENDOR_AUTHORIZATION_REQUIRED")
        if not files or len(files) > 20:
            raise ValueError("ONE_TO_TWENTY_PERMITTED_FILES_REQUIRED")
        from cutback.agent_preparation import begin, expiry

        preparation = begin(directory, state, "native")
        rights = CapturePolicy(
            enabled=True,
            approval_ref=rights_ref,
            rights_basis=rights_basis,
            environments=["staging"],
            workflows=[profile.binding.workflow_id],
            allowed_providers=sorted(providers),
            expires_at=expiry(preparation),
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
            candidates=campaign_basis_candidates(baseline)
            if for_campaign
            else search_candidates(baseline)
            if search
            else None,
        )
        if preparation and spec.content_nonce is not None:
            spec = spec.model_copy(update={"content_nonce": UUID(preparation["content_nonce"])})
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
                                kind="exact_label",
                                output_field=field,
                                minimum=minimum,
                                min_test_samples=min_test_samples,
                            ),
                        )
                    )
        spec = ExperimentPlan.model_validate(
            {
                **spec.model_dump(mode="json"),
                "metric": {
                    **spec.metric.model_dump(mode="json"),
                    "min_test_samples": min_test_samples,
                },
                "source_binding": profile.binding.model_dump(mode="json"),
                "criteria": [c.model_dump(mode="json") for c in criteria],
                "retain_case_outputs": retain_case_outputs,
                "split": "validation" if search or for_campaign else "test",
                **(
                    {"native_fixture": {"version": "native-source-fixture-v1"}}
                    if fixture_transport
                    else {}
                ),
                **({"name": "Campaign preparation basis"} if for_campaign else {}),
            }
        )
        summary = plan_summary(spec, prepared)
        if not summary["fits_budget"]:
            raise ValueError("PROPOSED_PLAN_EXCEEDS_BUDGET_OR_HAS_NO_HELDOUT_CASES")
        intent = {
            "source": profile.binding.profile_sha256,
            "examples": identities,
            "rights": rights_ref,
            "rights_basis": rights_basis,
            "project": project,
            "endpoint": control.endpoint,
            "spec": {**spec.model_dump(mode="json"), "dataset_hash": None},
        }
        if for_campaign:
            intent["campaign_basis"] = {"allowed_providers": rights.allowed_providers}
        if criteria_generator_provider:
            intent["criteria_generation"] = {"allowed_providers": rights.allowed_providers}
        from cutback.agent_preparation import bind

        identity = bind(fingerprint(intent), preparation, body, rights)
        if state["phase"] in {"PLAN_READY", "PREPARING"}:
            if state["preparation_sha256"] != identity:
                raise ValueError("FROZEN_PLAN_CHANGED_START_NEW_SESSION")
            if state["phase"] == "PLAN_READY":
                return public_session(state)
        base = f"/v1/projects/{project}"
        state.update(
            project_id=project,
            endpoint=control.endpoint,
            selected_workflow=body.workflow,
            preparation_sha256=identity,
            search_mode="campaign_basis"
            if for_campaign
            else "model_and_output_cap"
            if search
            else "qualified_model_swap",
            permitted_files=identities,
            rights_ref=rights_ref,
            quality_scope="heldout_independent_labels"
            if spec.metric.kind in {"field_f1", "exact_label"}
            else "technical_only_not_verified",
        )
        if for_campaign:
            state["campaign_allowed_providers"] = rights.allowed_providers
        if criteria_generator_provider:
            state["criteria_allowed_providers"] = rights.allowed_providers
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
            "search": search,
            "for_campaign": for_campaign,
            "campaign_generator_provider": campaign_generator_provider,
            "criteria_generator_provider": criteria_generator_provider,
            "min_test_samples": min_test_samples,
            "fixture_transport": fixture_transport,
        }
        save_session(directory, state, "PREPARING")
        from cutback import agent_preparation as setup

        setup.require_protocol(control, native_fixture=fixture_transport)
        inventory = read_private(directory / "inventory.json")
        setup.create(
            control,
            base,
            "scans",
            inventory,
            preparation,
            state=state,
            directory=directory,
            response_key="scan_id",
        )
        policy = setup.create(
            control,
            base,
            "capture-policies",
            rights.model_dump(mode="json"),
            preparation,
            state=state,
            directory=directory,
        )
        state["policy_id"] = policy["id"]
        save_session(directory, state)
        body = body.model_copy(update={"policy_id": policy["id"]})
        dataset = setup.create(
            control,
            base,
            "datasets",
            body.model_dump(mode="json"),
            preparation,
            state=state,
            directory=directory,
        )
        state["dataset_id"] = dataset["id"]
        state["dataset_split_counts"] = dataset["provenance"]["split_counts"]
        save_session(directory, state)
        spec = ExperimentPlan.model_validate(
            {**spec.model_dump(mode="json"), "dataset_hash": dataset["immutable_hash"]}
        )
        plan = setup.plan(
            control, base, dataset["id"], spec, preparation, state=state, directory=directory
        )
        state["plan"] = plan
        save_session(directory, state, "PLAN_READY")
        return public_session(state)


def advance(root, control, *, plan_hash=None, approval_ref=None):
    """One exact approval, one run, subsequent invocations only poll that run."""
    if load_session(root).get("search_mode") == "source_routing":
        from cutback.agent_routing import advance as routing_advance

        return routing_advance(root, control, plan_hash=plan_hash, approval_ref=approval_ref)
    if load_session(root)["phase"] in {
        "CRITERIA_PLAN_READY",
        "APPROVING_CRITERIA",
        "QUEUEING_CRITERIA",
        "CRITERIA_QUEUED",
        "CRITERIA_RUNNING",
        "CRITERIA_REVIEW_REQUIRED",
    }:
        from cutback.agent_criteria import advance as advance_criteria

        return advance_criteria(root, control, plan_hash=plan_hash, approval_ref=approval_ref)
    root, directory = workspace(root)
    with workspace_lock(directory):
        state = load_session(root)
        if state.get("endpoint") != control.endpoint:
            raise ValueError("COLLECTOR_DESTINATION_MISMATCH")
        base = f"/v1/projects/{state['project_id']}"
        if state["phase"] == "PLAN_READY":
            if state.get("search_mode") == "campaign_basis":
                raise ValueError(
                    "CAMPAIGN_BASIS_REQUIRES_CAMPAIGN_PREPARATION_NOT_STATIC_EXECUTION"
                )
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
            execution = control("GET", base + f"/experiments/plans/{state['plan']['id']}/execution")
            if execution["plan_id"] != state["plan"]["id"]:
                raise ValueError("PLAN_EXECUTION_IDENTITY_MISMATCH")
            if not execution["run_id"]:
                state["stop_code"] = "UNCERTAIN_APPROVAL_OR_QUEUE_RECONCILE_WITH_OPERATOR"
                save_session(directory, state)
                return public_session(state)
            state["run_id"] = str(UUID(execution["run_id"]))
            state.pop("stop_code", None)
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
                    if state.get("search_mode")
                    in {"model_and_output_cap", "joint_model_context", "reviewed_criteria"}
                    and not state.get("confirmation")
                    else "cutback workflow proposal --purpose-reference REVIEW"
                )
                save_session(directory, state)
        return public_session(state)


def gate(root, control, *, candidate=None, sample_only=False):
    """Read current canonical evidence and current source; no inference or content reads."""
    if load_session(root).get("search_mode") == "source_routing":
        raise ValueError("SOURCE_ROUTING_REQUIRES_CURRENT_PROPOSAL_AND_BOTH_TREE_REVIEW")
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
        if state.get("search_mode") == "reviewed_criteria" and state.get("confirmation"):
            current = control(
                "GET",
                f"/v1/projects/{state['project_id']}/criteria/"
                + state["criteria"]["id"]
                + "/evidence-validity?run_id="
                + state["run_id"],
            )
            if current.get("eligible") is not True:
                return {
                    "passed": False,
                    "reasons": ["current_reviewed_confirmation_unavailable"],
                    "candidate_keys": [],
                    "plan_hash": state["plan"]["plan_hash"],
                    "production_deployed": False,
                    "production_savings_usd": None,
                }
        if state.get("search_mode") == "joint_model_context":
            from cutback.evals.joint_context_planning import regression_gate as joint_gate

            return joint_gate(
                report["results"],
                expected_plan_hash=state["plan"]["plan_hash"],
                candidate=candidate,
                sample_only=sample_only,
            )
        return regression_gate(
            report["results"],
            expected_plan_hash=state["plan"]["plan_hash"],
            candidate=candidate,
            sample_only=sample_only,
        )


def confirm(root, control, *, candidate, max_usd, max_requests=100, purpose):
    """Freeze one validation winner for a separately approved untouched test run."""
    if load_session(root).get("search_mode") == "source_routing":
        raise ValueError("ROUTING_CONFIRMATION_IS_PREPARED_BY_RESUMABLE_CONTINUE")
    if load_session(root).get("search_mode") == "reviewed_criteria":
        from cutback.agent_criteria import confirm as confirm_criteria

        return confirm_criteria(
            root,
            control,
            candidate=candidate,
            max_usd=max_usd,
            max_requests=max_requests,
            purpose=purpose,
        )
    from cutback.evals.experiments import Budget, ExperimentPlan, plan_summary

    root, directory = workspace(root)
    with workspace_lock(directory):
        state = load_session(root)
        if state["endpoint"] != control.endpoint:
            raise ValueError("COLLECTOR_DESTINATION_MISMATCH")
        if (
            state["phase"] != "COMPLETED"
            or state.get("confirmation")
            or state.get("search_mode")
            not in {"model_and_output_cap", "joint_model_context", "reviewed_criteria"}
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
        if state.get("search_mode") == "joint_model_context":
            if decision.get("joint_model_context_confirmation_eligible") is not True:
                raise ValueError("JOINT_COMPONENT_COST_AND_QUALITY_GATES_REQUIRED")
            spec["compression_execution"]["variants"] = [
                value
                for value in spec["compression_execution"]["variants"]
                if value["model_key"] in {spec["baseline"]["key"], candidate}
            ]
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
    if load_session(root).get("search_mode") == "source_routing":
        from cutback.agent_routing import proposal as routing_proposal

        if candidate is not None:
            raise ValueError("ROUTING_CONFIRMATION_CANDIDATE_ALREADY_FROZEN")
        return routing_proposal(root, control, purpose=purpose)
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
        if state.get("search_mode") == "joint_model_context":
            from cutback.joint_context_changes import JointContextProposal, prepare_patch

            source = control(
                "POST", base + f"/model-context/{state['run_id']}/proposal", {"purpose": purpose}
            )
            proposed = JointContextProposal.model_validate(source)
            if candidate is not None and proposed.proposed.key != candidate:
                raise ValueError("SELECTED_JOINT_CONFIRMATION_CANDIDATE_CHANGED")
            artifact = prepare_patch(root, proposed)
            state["proposal"] = {
                "version": artifact["version"],
                "run_id": str(proposed.run_id),
                "proposed_files": artifact["proposed_files"],
            }
            save_session(directory, state, "PROPOSAL_READY")
            return artifact
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
            and r["evidence"].get("lineage", {}).get("fresh_test_claim") is True
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
        if state.get("search_mode") == "reviewed_criteria":
            choices[0] = control(
                "POST",
                base
                + f"/criteria/{state['criteria']['id']}/recommendations/"
                + f"{choices[0]['id']}/source-evidence",
                {"purpose": purpose},
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
