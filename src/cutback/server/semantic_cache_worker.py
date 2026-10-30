"""Bounded local cache calibration and one frozen confirmation; no SDK or inference call."""

import re
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import UUID, uuid4

from sqlalchemy import or_, select, true

from cutback.evals.datasets import digest
from cutback.evals.semantic_cache import CacheQualificationSpec, calibrate, confirm
from cutback.server.dataset_lineage import expose, inspect_exposure, try_lock_lineage
from cutback.server.db import AuditEvent, DatasetRecord
from cutback.server.principals import principal_role
from cutback.server.semantic_cache_db import SemanticCacheNamespace, SemanticCacheQualification
from cutback.server.semantic_cache_service import (
    authority,
    catalog,
    manifest,
    qualification_queries,
    read_catalog,
)

CLAIMABLE = {"QUEUED", "CALIBRATED", "CALIBRATING", "CONFIRMING"}


def _claim(sessions, identifier):
    now = datetime.now(UTC)
    with sessions() as session:
        candidates = session.execute(
            select(SemanticCacheQualification.id, SemanticCacheQualification.project_id)
            .where(
                SemanticCacheQualification.state.in_(CLAIMABLE),
                SemanticCacheQualification.id == identifier if identifier else true(),
                or_(
                    SemanticCacheQualification.lease_until.is_(None),
                    SemanticCacheQualification.lease_until <= now,
                ),
            )
            .order_by(SemanticCacheQualification.created_at, SemanticCacheQualification.id)
            .limit(20)
        ).all()
    for identity, project in candidates:
        with sessions.begin() as session:
            if not try_lock_lineage(session, project):
                continue
            row = session.get(SemanticCacheQualification, identity, with_for_update=True)
            if not row or row.state not in CLAIMABLE or row.lease_until and row.lease_until > now:
                continue
            if row.lease_token:
                row.active_uncertain_us += row.active_reserved_us
                row.active_reserved_us = 0
                row.lease_token = row.lease_until = None
                row.state = "FAILED"
                row.report = {
                    **row.report,
                    "code": "SEMANTIC_CACHE_LOCAL_WORK_INTERRUPTED_NO_NEW_SELECTION",
                }
                continue
            if row.expires_at <= now:
                row.state = "EXPIRED"
                continue
            remaining = row.specification["max_wall_seconds"] * 1000000 - row.active_elapsed_us
            if remaining <= 0:
                row.state = "FAILED"
                row.report = {**row.report, "code": "SEMANTIC_CACHE_LOCAL_WALL_BUDGET_EXHAUSTED"}
                continue
            reserved = min(60000000, remaining)
            row.active_reserved_us = reserved
            row.active_elapsed_us += reserved
            row.state = "CALIBRATING" if row.state == "QUEUED" else "CONFIRMING"
            row.lease_token = uuid4()
            row.lease_until = now + timedelta(microseconds=reserved + 30000000)
            row._started = monotonic()
            row._deadline = row._started + reserved / 1000000
            return row
    return None


def _current(session, row):
    live = session.get(SemanticCacheQualification, row.id, populate_existing=True)
    if (
        not live
        or live.lease_token != row.lease_token
        or live.state != row.state
        or live.expires_at <= datetime.now(UTC)
        or not live.lease_until
        or live.lease_until <= datetime.now(UTC)
        or monotonic() >= row._deadline
        or live.approval_hash != digest(live.specification)
        or principal_role(session, live.project_id, live.approved_by or "")
        not in {"admin", "project_admin"}
    ):
        raise ValueError("SEMANTIC_CACHE_QUALIFICATION_CURRENT_AUTHORITY_DENIED")
    ns = session.get(SemanticCacheNamespace, live.namespace_id, populate_existing=True)
    if (
        not ns
        or ns.project_id != live.project_id
        or ns.specification_hash != live.specification["namespace_hash"]
    ):
        raise ValueError("SEMANTIC_CACHE_QUALIFICATION_NAMESPACE_CHANGED")
    _, policy, _ = authority(session, ns)
    if digest(policy.model_dump(mode="json")) != live.specification["capture_policy_hash"]:
        raise ValueError("SEMANTIC_CACHE_CAPTURE_POLICY_CHANGED_REAPPROVAL_REQUIRED")
    dataset = session.get(DatasetRecord, live.dataset_id, populate_existing=True)
    if (
        not dataset
        or dataset.project_id != live.project_id
        or dataset.immutable_hash != live.specification["dataset_hash"]
    ):
        raise ValueError("SEMANTIC_CACHE_QUALIFICATION_DATASET_CHANGED")
    if (
        digest(live.catalog_manifest) != live.specification["catalog_hash"]
        or manifest(catalog(session, ns)) != live.catalog_manifest
    ):
        raise ValueError("SEMANTIC_CACHE_QUALIFICATION_CATALOG_CHANGED")
    return live, ns


def selection_hash(row, calibration):
    return digest(
        {
            "approval_hash": row.approval_hash,
            "catalog_hash": row.specification["catalog_hash"],
            "selected_threshold": calibration["selected_threshold"],
            "calibration": calibration,
        }
    )


def _save(sessions, row, state, report, selected=None):
    with sessions.begin() as session:
        live, _ = _current(session, row)
        live.state, live.report = state, report
        if selected is not None:
            live.selected_threshold = selected
        session.add(
            AuditEvent(
                project_id=row.project_id,
                actor="semantic_cache_worker",
                action="semantic_cache_qualification_advanced",
                details={"qualification_id": str(row.id), "state": state, "billable_calls": 0},
            )
        )


def run_semantic_cache_one(sessions, store, *, execution_enabled=False, only_qualification_id=None):
    if not execution_enabled:
        return {"status": "SEMANTIC_CACHE_QUALIFICATION_DISABLED", "billable_calls": 0}
    identifier = UUID(str(only_qualification_id)) if only_qualification_id else None
    row = _claim(sessions, identifier)
    if not row:
        return {"status": "NO_ELIGIBLE_SEMANTIC_QUALIFICATION", "billable_calls": 0}
    try:

        def bounded():
            if monotonic() >= row._deadline:
                raise ValueError("SEMANTIC_CACHE_QUALIFICATION_WALL_BOUND")

        spec = CacheQualificationSpec.model_validate(row.specification["qualification"])
        with sessions() as session:
            _, ns = _current(session, row)
            cases, _, _ = qualification_queries(
                session,
                store,
                ns,
                row.dataset_id,
                spec,
                actor="semantic_cache_worker",
                check=bounded,
            )
            anchors, _, _ = read_catalog(
                session, store, ns, expected=row.catalog_manifest, check=bounded
            )
        mode = "validation" if row.state == "CALIBRATING" else "test"
        source = "semantic-qualification:" + str(row.id) + ":" + mode
        with sessions.begin() as session:
            if not try_lock_lineage(session, row.project_id):
                raise ValueError("SEMANTIC_CACHE_LINEAGE_BUSY")
            _current(session, row)
            if mode == "test":
                calibration = row.report.get("calibration")
                if (
                    not calibration
                    or calibration["selected_threshold"] != row.selected_threshold
                    or row.report.get("selection_sha256") != selection_hash(row, calibration)
                ):
                    raise ValueError("SEMANTIC_CACHE_FROZEN_CALIBRATION_SELECTION_REQUIRED")
                inspect_exposure(
                    session,
                    row.project_id,
                    row.dataset_id,
                    "test",
                    source_ref=source,
                    require_fresh=True,
                )
            expose(session, row.project_id, row.dataset_id, mode, source)
        # Exposure is durable before scoring. A lost test step never creates a
        # fresh confirmation or licenses another threshold choice.
        if mode == "validation":
            result = calibrate(spec, anchors, cases, check=bounded)
            selected = result["selected_threshold"]
            state = "CALIBRATED" if selected is not None else "FAILED"
            report = {
                "calibration": result,
                "selection_sha256": selection_hash(row, result),
                "billable_calls": 0,
                "embedding_origin": "externally_asserted_supplied_vectors",
                "evidence_origin": row.specification["evidence_origin"],
                "realized_savings_usd": None,
            }
            _save(sessions, row, state, report, selected)
            return {"status": state, "billable_calls": 0}
        result = confirm(spec, anchors, cases, row.selected_threshold, check=bounded)
        with sessions.begin() as session:
            if not try_lock_lineage(session, row.project_id):
                raise ValueError("SEMANTIC_CACHE_LINEAGE_BUSY")
            _current(session, row)
            _, lineage = inspect_exposure(
                session,
                row.project_id,
                row.dataset_id,
                "test",
                source_ref=source,
                require_fresh=True,
            )
        passed = (
            result["passed"]
            and row.specification["independent_labels_declared"]
            and lineage["fresh_test_claim"]
        )
        report = {
            **row.report,
            "confirmation": result,
            "lineage": lineage,
            "independent_labels": "owner_declared_not_automatically_verified",
            "code": "SEMANTIC_CACHE_DECLARED_TASK_QUALIFICATION_PASSED"
            if passed
            else "SEMANTIC_CACHE_CONFIRMATION_OR_REFERENCE_GATE_FAILED",
        }
        _save(sessions, row, "PASSED" if passed else "FAILED", report)
        return {"status": "PASSED" if passed else "FAILED", "billable_calls": 0}
    except Exception as exc:
        code = str(exc)
        if not re.fullmatch(r"[A-Z0-9_]{1,120}", code):
            code = "SEMANTIC_CACHE_LOCAL_QUALIFICATION_FAILED_DETAILS_SUPPRESSED"
        with sessions.begin() as session:
            live = session.get(SemanticCacheQualification, row.id, with_for_update=True)
            if (
                live
                and live.lease_token == row.lease_token
                and live.state not in {"CANCELLED", "EXPIRED"}
            ):
                live.state, live.report = (
                    "FAILED",
                    {**live.report, "code": code, "billable_calls": 0},
                )
        return {"status": "FAILED", "code": code, "billable_calls": 0}
    finally:
        with sessions.begin() as session:
            live = session.get(SemanticCacheQualification, row.id, with_for_update=True)
            if live and live.lease_token == row.lease_token:
                if live.active_reserved_us:
                    spent = max(0, int((monotonic() - row._started) * 1000000))
                    live.active_elapsed_us += spent - live.active_reserved_us
                    live.active_reserved_us = 0
                live.lease_token = live.lease_until = None
