"""Connect a reviewed campaign to declared later task windows without inference."""

from uuid import UUID, uuid4

from cutback.agent import workspace
from cutback.agent_campaign import current, status
from cutback.core.request_profiles import fingerprint
from cutback.core.workspace import private_directory, read_private, workspace_lock, write_private


def reviewed_campaign(saved, row):
    review = saved.get("application_review")
    if not isinstance(review, dict) or (
        review.get("review_sha256")
        != fingerprint({k: v for k, v in review.items() if k != "review_sha256"})
        or review.get("status") != "READY_FOR_PR_REVIEW"
        or review.get("campaign_id") != saved["campaign_id"]
        or review.get("campaign_hash") != row["campaign_hash"]
    ):
        raise ValueError("CURRENT_FROZEN_CAMPAIGN_APPLICATION_REVIEW_REQUIRED")
    if row["status"] != "COMPLETED" or row.get("billing_complete") is not True:
        raise ValueError("COMPLETED_CAMPAIGN_WITH_KNOWN_OVERHEAD_REQUIRED")
    runs = row["runs"]
    version = row.get("specification", {}).get("version", "cutback.improvement-campaign.v1")
    minimum, maximum = (2, 8) if version == "cutback.improvement-campaign.v2" else (3, 7)
    if not minimum <= len(runs) <= maximum or len({r["run_id"] for r in runs}) != len(runs):
        raise ValueError("RETAINED_CAMPAIGN_RUN_FAMILY_REQUIRED")
    confirmations = [r for r in runs if r["phase"] == "confirmation"]
    if len(confirmations) != 1 or (
        confirmations[0]["status"] != "COMPLETED"
        or confirmations[0]["run_id"] != review.get("run_id")
        or confirmations[0]["plan_hash"] != review.get("plan_hash")
        or any(r["status"] not in {"COMPLETED", "FAILED", "CANCELLED"} for r in runs)
        or any(r["known_cost_requests"] != r["requests"] for r in runs)
        or sum(r["requests"] for r in runs) != row["requests"]
    ):
        raise ValueError("CAMPAIGN_CONFIRMATION_OR_COST_FAMILY_CHANGED")
    return review, sorted(str(UUID(r["run_id"])) for r in runs)


def declared_change(control, base, identifier):
    # The existing metadata endpoint has fixed 100-row pages and a 10,000 offset
    # ceiling. Never read encrypted diff content or scan an unbounded collection.
    for offset in range(0, 10001, 100):
        page = control("GET", f"{base}/changes?offset={offset}")
        if not isinstance(page, list) or len(page) > 100:
            raise ValueError("CHANGE_METADATA_PAGE_CONTRACT_CHANGED")
        matches = [r for r in page if r["id"] == identifier]
        if matches:
            return matches[0]
        if len(page) < 100:
            break
    raise ValueError("REVIEWED_CHANGE_NOT_FOUND_WITHIN_METADATA_BOUND")


def record_outcomes(
    root,
    control,
    *,
    candidate_snapshot_id,
    change_id,
    release_linkage_attested=False,
    assumptions=None,
    observation_id=None,
):
    """Freeze a canonical comparison and a review action; never deploy or call a model.

    Deployment linkage and completeness of costs outside the campaign remain owner
    declarations. A local review hash is an integrity check, not remote attestation.
    """
    from cutback.core.improvement_economics import ImprovementAssumptions

    if release_linkage_attested is not True:
        raise ValueError("EXPLICIT_CHANGE_TO_APPLICATION_RELEASE_ATTESTATION_REQUIRED")
    candidate_snapshot_id, change_id = (
        str(UUID(str(candidate_snapshot_id))),
        str(UUID(str(change_id))),
    )
    if observation_id is not None:
        observation_id = str(UUID(str(observation_id)))
        if not UUID(observation_id).int:
            raise ValueError("NONZERO_OBSERVATION_ID_REQUIRED")
    assumptions = ImprovementAssumptions.model_validate(
        {} if assumptions is None else assumptions
    ).model_dump(mode="json")
    root, directory = workspace(root)
    with workspace_lock(directory):
        saved = current(root, control)
        row = status(root, control)
        review, runs = reviewed_campaign(saved, row)
        base = f"/v1/projects/{saved['project_id']}"
        change = declared_change(control, base, change_id)
        if (
            change["evidence"].get("diff_sha256") != review["diff_sha256"]
            or change["evidence"].get("run_id") != review["run_id"]
        ):
            raise ValueError("REVIEWED_CHANGE_DOES_NOT_MATCH_CAMPAIGN_DIFF_AND_CONFIRMATION")
        specification = {
            "baseline_snapshot_id": saved["request"]["baseline_snapshot_id"],
            "candidate_snapshot_id": candidate_snapshot_id,
            "change_id": change_id,
            "release_linkage_attested": True,
            "optimization_run_ids": runs,
            "assumptions": assumptions,
        }
        context = {
            "contract": "cutback.campaign-outcome-request.v1",
            "endpoint": control.endpoint,
            "project_id": saved["project_id"],
            "campaign_id": saved["campaign_id"],
            "campaign_hash": row["campaign_hash"],
            "application_review_sha256": review["review_sha256"],
            "specification": specification,
            "explicit_observation_id": observation_id,
        }
        location = private_directory(root, ".cutback/agent/campaign-observations")
        # Keep filenames short in nested Windows applications. A collision is
        # refused by the full context comparison, never treated as authority.
        path = location / (fingerprint(context)[:24] + ".json")
        if path.exists():
            checkpoint = read_private(path)
            if checkpoint["context"] != context:
                raise ValueError("CAMPAIGN_OBSERVATION_CHECKPOINT_CONFLICT")
        else:
            checkpoint = {
                "context": context,
                "observation_id": observation_id or str(uuid4()),
            }
            # Persist identity before sending. A lost response repeats this exact
            # nonbillable request; the server's project/request uniqueness wins.
            write_private(path, checkpoint, exclusive=True)
        receipt = control(
            "POST",
            base + "/improvement-observations",
            {**specification, "observation_id": checkpoint["observation_id"]},
        )
        evidence = receipt["evidence"]
        if (
            fingerprint(evidence) != receipt["evidence_sha256"]
            or evidence["source_diff_sha256"] != review["diff_sha256"]
            or evidence["change_id"] != change_id
            or evidence["baseline_snapshot_id"] != specification["baseline_snapshot_id"]
            or evidence["candidate_snapshot_id"] != candidate_snapshot_id
            or sorted(r["run_id"] for r in evidence["run_costs"]) != runs
        ):
            raise ValueError("CAMPAIGN_OBSERVATION_RECEIPT_BINDING_MISMATCH")
        report = evidence["report"]
        reasons = report["reasons"]
        action = (
            "REVIEW_ROLLBACK"
            if report["descriptive_outcome_non_regression"] is False
            else "REVIEW_COST_REGRESSION"
            if "no_observed_modeled_cost_improvement" in reasons
            else "COLLECT_MISSING_EVIDENCE"
            if reasons
            else "PREPARE_INDEPENDENT_CONFIRMATION"
        )
        write_private(
            path,
            {
                **checkpoint,
                "receipt_id": receipt["id"],
                "evidence_sha256": receipt["evidence_sha256"],
            },
        )
        return {
            "contract": "cutback.campaign-outcome-review.v1",
            "campaign_id": saved["campaign_id"],
            "campaign_hash": row["campaign_hash"],
            "application_review_sha256": review["review_sha256"],
            "receipt": receipt,
            "next_action": action,
            "action_authorized": False,
            "release_linkage": "owner_attested_not_deployment_verified",
            "quality_evidence": report["quality_evidence"],
            "causal_attribution": report["causal_attribution"],
            "provider_calls": 0,
            "traffic_changed": False,
        }
