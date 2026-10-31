"""Agent-facing resumable campaign preparation; metadata only in the local checkpoint."""

from decimal import Decimal
from uuid import UUID, uuid4

from cutback.agent import load_session, selected_profile, workspace
from cutback.core.workspace import read_private, workspace_lock, write_private


def prepare(
    root,
    control,
    *,
    baseline_snapshot_id,
    max_usd,
    max_requests=500,
    savings_per_success=None,
    forecast_successful_tasks=None,
    generator_model="gpt-4.1-mini",
    rounds=1,
    variants_per_round=2,
    simple_first=None,
):
    root, directory = workspace(root)
    with workspace_lock(directory):
        state = load_session(root)
        if state["phase"] != "PLAN_READY" or state.get("endpoint") != control.endpoint:
            raise ValueError("PREPARED_SOURCE_BOUND_WORKFLOW_REQUIRED")
        profile = selected_profile(root, state, state["selected_workflow"])
        from cutback.core.instruction_edits import qualify_instruction_edit

        inventory = read_private(directory / "inventory.json")
        site = next(
            s for s in inventory["call_sites"] if s["call_site_id"] == profile.binding.call_site_id
        )
        try:
            edit = qualify_instruction_edit(root, site, profile)
        except ValueError as exc:
            if str(exc) not in {
                "INSTRUCTION_EDIT_REQUIRES_QUALIFIED_SAME_FILE_REQUEST",
                "DYNAMIC_INSTRUCTION_EDIT_REQUIRES_SEPARATE_ADAPTER",
                "INSTRUCTION_EDIT_NATIVE_SURFACE_NOT_QUALIFIED",
                "INSTRUCTION_EDIT_REQUIRES_LITERAL_BOUND_MODEL",
            }:
                raise
            edit = None
        path = directory / "campaign.json"
        saved = read_private(path) if path.exists() else None
        version = (
            saved["request"].get("version", "cutback.improvement-campaign.v1")
            if simple_first is None and saved
            else "cutback.improvement-campaign.v1"
            if simple_first is False
            else "cutback.improvement-campaign.v3"
            if simple_first is None
            else "cutback.improvement-campaign.v2"
        )
        if state.get("workload_portfolio_sha256"):
            from cutback.evals.campaigns import WORKLOAD_CAMPAIGN_VERSION

            if simple_first is False:
                raise ValueError("WORKLOAD_CACHE_CAMPAIGN_REQUIRES_SIMPLE_FIRST")
            if control("GET", "/v1/health/ready").get("workload_campaign_contract") != (
                WORKLOAD_CAMPAIGN_VERSION
            ):
                raise ValueError("COLLECTOR_WORKLOAD_CAMPAIGN_UPGRADE_REQUIRED")
            version = WORKLOAD_CAMPAIGN_VERSION
        body = {
            "version": version,
            "template_plan_id": state["plan"]["id"],
            "instruction_edit": edit.model_dump(mode="json") if edit else None,
            "baseline_snapshot_id": str(UUID(str(baseline_snapshot_id))),
            "generator_model": {
                "key": "proposer",
                "provider": "openai",
                "model": generator_model,
                "max_input_tokens": 24000,
                "max_output_tokens": 2048,
            },
            "budget": {"max_usd": str(Decimal(max_usd)), "max_requests": max_requests},
            "conservative_savings_per_success_usd": savings_per_success,
            "forecast_successful_tasks": forecast_successful_tasks,
            "rounds": rounds,
            "variants_per_round": variants_per_round,
        }
        if generator_model == "gpt-6-luna":
            from cutback.core.native_models import NativeModelContract

            body["generator_model"]["native_contract"] = (
                NativeModelContract.current_luna_text().model_dump(mode="json")
            )
        if saved:
            previous = {"version": "cutback.improvement-campaign.v1", **saved["request"]}
            if previous != body:
                raise ValueError("SAVED_CAMPAIGN_SCOPE_CHANGED_NEW_WORKFLOW_REQUIRED")
        else:
            saved = {
                "request": body,
                "request_id": str(uuid4()),
                "project_id": state["project_id"],
                "endpoint": control.endpoint,
                "workflow": state["selected_workflow"],
            }
            write_private(path, saved)
        receipt = control(
            "POST",
            f"/v1/projects/{saved['project_id']}/campaigns/draft",
            {**saved["request"], "request_id": saved["request_id"]},
        )
        saved["campaign_id"] = receipt["id"]
        write_private(path, saved)
        return {
            **receipt,
            "next_action": "review bounds, then use cutback campaign continue with the exact hash",
            "provider_calls_made_in_cli": 0,
        }


def current(root, control):
    _, directory = workspace(root)
    saved = read_private(directory / "campaign.json")
    if saved["endpoint"] != control.endpoint or not saved.get("campaign_id"):
        raise ValueError("SAVED_CAMPAIGN_OR_DESTINATION_REQUIRED")
    return saved


def status(root, control):
    saved = current(root, control)
    return control("GET", f"/v1/projects/{saved['project_id']}/campaigns/{saved['campaign_id']}")


def advance(root, control, *, campaign_hash=None, approval_ref=None):
    root, directory = workspace(root)
    with workspace_lock(directory):
        saved = current(root, control)
        state = load_session(root)
        selected_profile(root, state, saved["workflow"])
        row = status(root, control)
        if row["status"] == "READY_FOR_APPROVAL":
            if campaign_hash != row["campaign_hash"] or not approval_ref:
                raise ValueError("EXACT_CAMPAIGN_HASH_AND_APPROVAL_REFERENCE_REQUIRED")
            return control(
                "POST",
                f"/v1/projects/{saved['project_id']}/campaigns/{saved['campaign_id']}/approve",
                {"campaign_hash": campaign_hash, "approval_ref": approval_ref},
            )
        # A lost approval response is reconciled by reading the same identity.
        # Never allocate another campaign or silently approve a different hash.
        return row


def proposal(root, control, *, purpose, sample_only=False):
    """Prepare the exact confirmed change with rollback; never apply or deploy it."""
    from cutback.changes import instruction_patch, model_patch
    from cutback.core.instruction_edits import LiteralInstructionEdit
    from cutback.evals.datasets import digest
    from cutback.evals.experiments import ExperimentPlan
    from cutback.evals.search import regression_gate

    root, directory = workspace(root)
    with workspace_lock(directory):
        saved = current(root, control)
        state = load_session(root)
        profile = selected_profile(root, state, saved["workflow"])
        base = f"/v1/projects/{saved['project_id']}"
        context = control(
            "POST",
            base + f"/campaigns/{saved['campaign_id']}/selected/content",
            {"purpose": purpose},
        )
        if context["campaign_id"] != saved["campaign_id"]:
            raise ValueError("CAMPAIGN_SELECTED_CONTENT_SCOPE_MISMATCH")
        plan = ExperimentPlan.model_validate(context["plan"])
        if (
            digest(plan.model_dump(mode="json")) != context["plan_hash"]
            or plan.source_binding != profile.binding
        ):
            raise ValueError("CAMPAIGN_CONFIRMED_PLAN_OR_SOURCE_CHANGED")
        report = control("GET", base + f"/experiments/{context['run_id']}")
        gate = regression_gate(
            report["results"], expected_plan_hash=context["plan_hash"], sample_only=sample_only
        )
        if not gate["passed"]:
            raise ValueError("CAMPAIGN_INDEPENDENT_CONFIRMATION_GATE_REQUIRED")
        recommendation = {
            "id": context["recommendation_id"],
            "run_id": context["run_id"],
            "status": context["recommendation_status"],
            "evidence": context["evidence"],
        }
        options = {
            "expected_source_sha256": profile.binding.dependencies[
                profile.binding.model_binding["file"]
            ],
            "proposal_only": True,
        }
        inventory = read_private(directory / "inventory.json")
        candidate = context["evidence"]["candidate"]
        if candidate["instruction"] != plan.baseline.instruction:
            if context["instruction_edit"] is None:
                raise ValueError("CAMPAIGN_INSTRUCTION_EDIT_WAS_NOT_QUALIFIED_BEFORE_APPROVAL")
            artifact = instruction_patch(
                root,
                inventory,
                profile.binding.call_site_id,
                recommendation,
                instruction_edit=LiteralInstructionEdit.model_validate(context["instruction_edit"]),
                max_instruction_bytes=context["max_instruction_bytes"],
                **options,
            )
        else:
            artifact = model_patch(
                root, inventory, profile.binding.call_site_id, recommendation, **options
            )
        saved["proposal"] = {
            key: artifact[key]
            for key in ("diff_sha256", "source_file", "recommendation_id", "run_id")
        }
        saved["proposal"]["evidence_scope"] = (
            "synthetic_or_sample_only" if sample_only else "independent_heldout_dataset"
        )
        write_private(directory / "campaign.json", saved)
        return {
            **artifact,
            "campaign_id": saved["campaign_id"],
            "campaign_hash": context["campaign_hash"],
            "confirmation_gate": gate,
            "application_checks_required": True,
        }


def review(
    root,
    control,
    *,
    purpose,
    contract,
    receipt,
    base,
    proposed,
    coverage,
    branch=None,
    sample_only=False,
):
    """Refresh confirmed evidence and bind completed local checks to the exact change."""
    from cutback.campaign_review import review_change

    artifact = proposal(root, control, purpose=purpose, sample_only=sample_only)
    result = review_change(artifact, contract, receipt, base, proposed, coverage, branch=branch)
    root, directory = workspace(root)
    with workspace_lock(directory):
        saved = current(root, control)
        saved["application_review"] = result
        write_private(directory / "campaign.json", saved)
    return result
