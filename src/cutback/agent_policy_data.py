"""Bounded metadata discovery for source-policy preparation, without content access."""

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from uuid import UUID

from pydantic import TypeAdapter

from cutback.core.models import Identifier
from cutback.core.request_profiles import fingerprint

CONTRACT = "cutback.source-policy-data-discovery.v1"


def _id(value):
    return str(UUID(str(value)))


def _hash(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("POLICY_DATA_METADATA_HASH_INVALID")
    return value


def _complete(summary):
    if not isinstance(summary, dict):
        return False
    try:
        if type(summary["declared_window_cost_per_success_usd"]) is not str:
            return False
        amount = Decimal(summary["declared_window_cost_per_success_usd"])
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return False
    return (
        summary.get("window_cost_complete_under_declaration") is True
        and summary.get("task_count_matches_declaration") is True
        and type(summary.get("declared_successes")) is int
        and summary["declared_successes"] > 0
        and amount.is_finite()
        and amount >= 0
    )


def discover(root, control, project, *, producer_file, environment="staging", fixture=False):
    """Find compatible retained metadata; this does not attest source or data quality.

    A full page or remaining cursor keeps the result incomplete. Never select a
    newest version implicitly, manufacture missing labels, or read encrypted cases.
    Preparation still enforces current content rights, expiry and lineage.
    """
    from cutback.policy_source import inspect_configuration

    if type(fixture) is not bool:
        raise ValueError("POLICY_EXPLICIT_TRANSPORT_BOOLEAN_REQUIRED")
    project = _id(project)
    environment = TypeAdapter(Identifier).validate_python(environment)
    original, source = inspect_configuration(root, producer_file)
    base = f"/v1/projects/{project}"
    page = control("GET", base + "/environments?limit=100")
    if (
        not isinstance(page, dict)
        or type(page.get("items")) is not list
        or len(page["items"]) > 100
    ):
        raise ValueError("POLICY_DATA_BOUNDED_ENVIRONMENT_PAGE_REQUIRED")
    matches = [r for r in page["items"] if isinstance(r, dict) and r.get("name") == environment]
    if len(matches) != 1:
        raise ValueError("POLICY_DATA_EXPLICIT_EXISTING_ENVIRONMENT_REQUIRED")
    environment_id = _id(matches[0]["id"])
    datasets = control("GET", base + "/datasets?limit=100&offset=0")
    if type(datasets) is not list or len(datasets) > 100:
        raise ValueError("POLICY_DATA_BOUNDED_DATASET_PAGE_REQUIRED")
    choices, seen = [], set()
    for row in datasets:
        if not isinstance(row, dict):
            raise ValueError("POLICY_DATA_METADATA_PAGE_INVALID")
        identifier = _id(row["id"])
        if identifier in seen:
            raise ValueError("POLICY_DATA_DUPLICATE_METADATA_ID")
        seen.add(identifier)
        provenance = row.get("provenance")
        if not isinstance(provenance, dict):
            continue
        counts = provenance.get("split_counts", {})
        if (
            row.get("workflow") == source.workflow
            and provenance.get("environment") == environment
            and provenance.get("independent_labels") is True
            and (not fixture or provenance.get("source_type") == "synthetic_fixture")
            and isinstance(counts, dict)
            and all(
                type(counts.get(split)) is int and counts[split] > 0
                for split in ("validation", "test")
            )
        ):
            choices.append({"id": identifier, "hash": _hash(row["hash"])})
    snapshots, seen, cursor, exhausted = [], set(), None, False
    for _ in range(5):
        path = base + f"/task-cost-snapshots?environment_id={environment_id}&limit=20"
        if cursor is not None:
            path += "&after=" + cursor
        page = control("GET", path)
        if (
            not isinstance(page, dict)
            or type(page.get("items")) is not list
            or len(page["items"]) > 20
        ):
            raise ValueError("POLICY_DATA_BOUNDED_SNAPSHOT_PAGE_REQUIRED")
        for row in page["items"]:
            if not isinstance(row, dict):
                raise ValueError("POLICY_DATA_METADATA_PAGE_INVALID")
            identifier = _id(row["id"])
            if identifier in seen or row.get("environment_id") != environment_id:
                raise ValueError("POLICY_DATA_SCOPED_SNAPSHOT_PAGE_REQUIRED")
            seen.add(identifier)
            if row.get("workflow") == source.workflow and _complete(row.get("summary")):
                snapshots.append(
                    {"id": identifier, "evidence_sha256": _hash(row["evidence_sha256"])}
                )
        next_cursor = page.get("next_after")
        if next_cursor is None:
            exhausted = True
            break
        next_cursor = _id(next_cursor)
        if (
            not page["items"]
            or next_cursor != _id(page["items"][-1]["id"])
            or next_cursor == cursor
        ):
            raise ValueError("POLICY_DATA_SNAPSHOT_CURSOR_INVALID")
        cursor = next_cursor
    dataset_complete = len(datasets) < 100
    selected_dataset = choices[0]["id"] if dataset_complete and len(choices) == 1 else None
    selected_snapshot = snapshots[0]["id"] if exhausted and len(snapshots) == 1 else None
    if inspect_configuration(root, producer_file) != (original, source):
        raise ValueError("POLICY_DATA_SOURCE_CHANGED_DURING_DISCOVERY")
    return {
        "version": CONTRACT,
        "project_id": project,
        "environment": environment,
        "environment_id": environment_id,
        "workflow": source.workflow,
        "source_binding_sha256": fingerprint(source.model_dump(mode="json")),
        "datasets": choices[:20],
        "dataset_candidates": len(choices),
        "dataset_listing_complete": dataset_complete,
        "baseline_snapshots": snapshots[:20],
        "baseline_candidates": len(snapshots),
        "baseline_listing_complete": exhausted,
        "selected_dataset_id": selected_dataset,
        "selected_baseline_snapshot_id": selected_snapshot,
        "status": "UNIQUE_COMPATIBLE_METADATA"
        if selected_dataset and selected_snapshot
        else "EXPLICIT_SELECTION_OR_MISSING_EVIDENCE",
        "provider_calls": 0,
        "encrypted_storage_reads": 0,
        "paid_calls_authorized": False,
        "source_change_authorized": False,
        "source_aligned_baseline_verified": False,
        "independent_quality_verified": False,
        "population_coverage": "unknown",
        "production_savings_usd": None,
    }


def verify_baseline(control, project, identifier, discovery):
    """Refresh one chosen frozen report and require its actual scoped identities."""
    chosen = next(row for row in discovery["baseline_snapshots"] if row["id"] == identifier)
    row = control("GET", f"/v1/projects/{project}/task-cost-snapshots/{identifier}")
    if not isinstance(row, dict) or not isinstance(row.get("report"), dict):
        raise ValueError("POLICY_DATA_CURRENT_FROZEN_BASELINE_REQUIRED")
    report = row.get("report", {})
    # Task snapshots use the released server JSON hashing convention. Keep its
    # ASCII escaping; request-profile hashing uses a different convention.
    digest = hashlib.sha256(
        json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if (
        row.get("id") != identifier
        or row.get("environment_id") != discovery["environment_id"]
        or report.get("environment_id") != discovery["environment_id"]
        or row.get("evidence_sha256") != chosen["evidence_sha256"]
        or digest != row.get("evidence_sha256")
        or not _complete(report.get("summary"))
        or type(report.get("items")) is not list
        or not 1 <= len(report["items"]) <= 1000
        or any(
            not isinstance(item, dict) or not isinstance(item.get("declaration"), dict)
            for item in report["items"]
        )
    ):
        raise ValueError("POLICY_DATA_CURRENT_FROZEN_BASELINE_REQUIRED")
    identities = {
        (
            item["declaration"].get("workflow"),
            item["declaration"].get("service"),
            item["declaration"].get("release"),
        )
        for item in report["items"]
    }
    if (
        len(identities) != 1
        or next(iter(identities))[0] != discovery["workflow"]
        or next(iter(identities))[2] is None
    ):
        raise ValueError("POLICY_DATA_ONE_WORKFLOW_SERVICE_RELEASE_REQUIRED")
