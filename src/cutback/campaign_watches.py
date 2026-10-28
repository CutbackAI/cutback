"""Agent preparation and exact approval of finite, metadata-only outcome policies."""

from uuid import UUID, uuid4

from cutback.agent import workspace
from cutback.agent_campaign import current
from cutback.agent_campaign import status as campaign_status
from cutback.campaign_outcomes import declared_change, reviewed_campaign
from cutback.core.outcome_watches import OutcomeWatchEnvelope, OutcomeWatchIntent
from cutback.core.request_profiles import fingerprint
from cutback.core.workspace import private_directory, read_private, workspace_lock, write_private


def validate_receipt(receipt, expected, diff_hash, runs):
    envelope = OutcomeWatchEnvelope.model_validate(receipt["specification"])
    actual = envelope.model_dump(mode="json")
    policy = envelope.policy.model_dump(mode="json")
    intent = {
        key: value
        for key, value in policy.items()
        if key not in {"baseline_sha256", "source_diff_sha256", "optimization_run_ids"}
    }
    if (
        fingerprint(actual) != receipt["approval_hash"]
        or str(envelope.project_id) != expected["project_id"]
        or str(envelope.environment_id) != expected["environment_id"]
        or intent != expected["intent"]
        or envelope.policy.source_diff_sha256 != diff_hash
        or sorted(str(identifier) for identifier in envelope.policy.optimization_run_ids) != runs
        or receipt["environment_id"] != expected["environment_id"]
        or (expected.get("receipt_id") is not None and receipt["id"] != expected["receipt_id"])
        or (
            expected.get("approval_hash") is not None
            and receipt["approval_hash"] != expected["approval_hash"]
        )
    ):
        raise ValueError("OUTCOME_WATCH_DERIVED_RECEIPT_BINDING_MISMATCH")
    return receipt


def prepare(
    root,
    control,
    *,
    change_id,
    candidate_release,
    first_window_end,
    expires_at,
    window_seconds,
    interval_seconds,
    grace_seconds=300,
    max_windows=30,
    expected_tasks=None,
    assumptions=None,
    release_linkage_attested=False,
    key="release-observation",
    watch_id=None,
):
    if release_linkage_attested is not True:
        raise ValueError("EXPLICIT_CHANGE_TO_APPLICATION_RELEASE_ATTESTATION_REQUIRED")
    change_id = str(UUID(str(change_id)))
    root, directory = workspace(root)
    with workspace_lock(directory):
        saved = current(root, control)
        row = campaign_status(root, control)
        review, runs = reviewed_campaign(saved, row)
        base = f"/v1/projects/{saved['project_id']}"
        change = declared_change(control, base, change_id)
        if (
            change["evidence"].get("diff_sha256") != review["diff_sha256"]
            or change["evidence"].get("run_id") != review["run_id"]
        ):
            raise ValueError("REVIEWED_CHANGE_DOES_NOT_MATCH_CAMPAIGN_DIFF_AND_CONFIRMATION")
        raw = {
            "key": key,
            "baseline_snapshot_id": saved["request"]["baseline_snapshot_id"],
            "change_id": change_id,
            "candidate_release": candidate_release,
            "first_window_end": first_window_end,
            "expires_at": expires_at,
            "window_seconds": window_seconds,
            "interval_seconds": interval_seconds,
            "grace_seconds": grace_seconds,
            "max_windows": max_windows,
            "expected_tasks": expected_tasks,
            "assumptions": assumptions or {},
            "release_linkage_attested": True,
        }
        raw = OutcomeWatchIntent.model_validate({**raw, "watch_id": UUID(int=1)}).model_dump(
            mode="json"
        )
        raw.pop("watch_id")
        context = {
            "contract": "cutback.campaign-watch-request.v1",
            "endpoint": control.endpoint,
            "project_id": saved["project_id"],
            "environment_id": row["environment_id"],
            "campaign_id": saved["campaign_id"],
            "campaign_hash": row["campaign_hash"],
            "application_review_sha256": review["review_sha256"],
            "intent": raw,
            "explicit_watch_id": str(UUID(str(watch_id))) if watch_id else None,
        }
        location = private_directory(root, ".cutback/agent/campaign-watches")
        path = location / (fingerprint(context)[:24] + ".json")
        if path.exists():
            checkpoint = read_private(path)
            if checkpoint["context"] != context:
                raise ValueError("CAMPAIGN_WATCH_CHECKPOINT_CONFLICT")
        else:
            checkpoint = {
                "context": context,
                "watch_id": context["explicit_watch_id"] or str(uuid4()),
            }
            write_private(path, checkpoint, exclusive=True)
        intent = OutcomeWatchIntent.model_validate(
            {**raw, "watch_id": checkpoint["watch_id"]}
        ).model_dump(mode="json")
        expected = {
            "project_id": saved["project_id"],
            "environment_id": row["environment_id"],
            "intent": intent,
        }
        receipt = control(
            "POST", f"{base}/outcome-watches/draft?environment_id={row['environment_id']}", intent
        )
        validate_receipt(receipt, expected, review["diff_sha256"], runs)
        value = {
            **checkpoint,
            "expected": {
                **expected,
                "receipt_id": receipt["id"],
                "approval_hash": receipt["approval_hash"],
            },
            "diff_sha256": review["diff_sha256"],
            "run_ids": runs,
            "receipt_id": receipt["id"],
            "approval_hash": receipt["approval_hash"],
        }
        write_private(path, value)
        write_private(directory / "campaign-watch.json", value)
        return {
            "contract": "cutback.campaign-watch-draft.v1",
            "receipt": receipt,
            "next_action": "REVIEW_EXACT_METADATA_OBSERVATION_POLICY",
            "provider_calls": 0,
            "traffic_changes_authorized": False,
        }


def saved_watch(root, control):
    root, directory = workspace(root)
    campaign = current(root, control)
    saved = read_private(directory / "campaign-watch.json")
    context = saved["context"]
    if (context["endpoint"], context["project_id"], context["campaign_id"]) != (
        control.endpoint,
        campaign["project_id"],
        campaign["campaign_id"],
    ):
        raise ValueError("SAVED_CAMPAIGN_WATCH_DESTINATION_CHANGED")
    return root, directory, saved


def status(root, control, *, after=None):
    if after is not None and (type(after) is not int or after < 0):
        raise ValueError("INVALID_OUTCOME_WATCH_WINDOW_CURSOR")
    _, _, saved = saved_watch(root, control)
    base = f"/v1/projects/{saved['context']['project_id']}/outcome-watches/{saved['receipt_id']}"
    receipt = control("GET", base)
    validate_receipt(receipt, saved["expected"], saved["diff_sha256"], saved["run_ids"])
    page = control(
        "GET", base + "/windows?limit=20" + (f"&after={int(after)}" if after is not None else "")
    )
    items, cursor = page.get("items"), page.get("next_after")
    if (
        not isinstance(items, list)
        or len(items) > 20
        or (cursor is not None and (type(cursor) is not int or cursor < 0))
    ):
        raise ValueError("OUTCOME_WATCH_WINDOW_PAGE_CONTRACT_CHANGED")
    previous_index = after
    for item in items:
        index = item["evidence"].get("window_index")
        if (
            fingerprint(item["evidence"]) != item["evidence_sha256"]
            or item["evidence"]["approval_hash"] != saved["approval_hash"]
            or type(item["evidence"]["provider_calls"]) is not int
            or item["evidence"]["provider_calls"] != 0
            or item["evidence"]["action_authorized"] is not False
            or item["evidence"]["traffic_changed"] is not False
            or type(index) is not int
            or index < 0
            or (previous_index is not None and index >= previous_index)
        ):
            raise ValueError("OUTCOME_WATCH_WINDOW_RECEIPT_BINDING_MISMATCH")
        previous_index = index
    if cursor is not None and (not items or cursor != previous_index):
        raise ValueError("OUTCOME_WATCH_WINDOW_PAGE_CONTRACT_CHANGED")
    return {
        "contract": "cutback.campaign-watch-status.v1",
        "receipt": receipt,
        "windows": page,
        "provider_calls": 0,
        "traffic_changed": False,
    }


def approve(root, control, *, approval_hash, approval_ref):
    root, directory = workspace(root)
    with workspace_lock(directory):
        _, _, saved = saved_watch(root, control)
        if approval_hash != saved["approval_hash"] or not approval_ref:
            raise ValueError("EXACT_OUTCOME_WATCH_HASH_AND_APPROVAL_REFERENCE_REQUIRED")
        receipt = control(
            "POST",
            f"/v1/projects/{saved['context']['project_id']}/outcome-watches/{saved['receipt_id']}/approve",
            {"approval_hash": approval_hash, "approval_ref": approval_ref},
        )
        validate_receipt(receipt, saved["expected"], saved["diff_sha256"], saved["run_ids"])
        return {
            "contract": "cutback.campaign-watch-approval.v1",
            "receipt": receipt,
            "provider_calls": 0,
            "traffic_changes_authorized": False,
        }
