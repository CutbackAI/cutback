"""Explicit curated-cache controls; qualification executes only in the optional worker."""

import json
import os
import re
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException, Query
from pydantic import JsonValue
from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError

from cutback.core.models import StrictModel
from cutback.core.semantic_cache import SemanticCacheIndex, unit_vector
from cutback.evals.datasets import Case, digest
from cutback.server.campaign_content import content_transaction
from cutback.server.content_policy import policy_content
from cutback.server.dataset_lineage import (
    inspect_exposure,
    lock_lineage,
    register_groups,
    require_unreserved_groups,
)
from cutback.server.db import (
    AccessKey,
    AuditEvent,
    CapturePolicyRecord,
    DatasetItem,
    Environment,
    PayloadSample,
)
from cutback.server.enterprise_db import CacheEntry
from cutback.server.evaluation_api import ContentPurpose, policy_object_count, require_store
from cutback.server.lineage_db import DatasetExposure
from cutback.server.principals import principal_role
from cutback.server.semantic_cache_db import (
    SemanticCacheMember,
    SemanticCacheNamespace,
    SemanticCacheQualification,
)
from cutback.server.semantic_cache_models import (
    NamespaceInput,
    QualificationPrepare,
    SemanticApproval,
    SemanticControl,
    SemanticLookup,
    SemanticPut,
)
from cutback.server.semantic_cache_service import (
    authority,
    catalog,
    manifest,
    namespace,
    qualification_queries,
    query_key,
    read_catalog,
)


class NamespaceReceipt(StrictModel):
    id: UUID
    state: str
    specification: dict[str, JsonValue]
    specification_hash: str
    embedding_contract_sha256: str
    entry_count: int
    active_qualification_id: UUID | None
    active_approval_hash: str | None
    expires_at: datetime


class QualificationReceipt(StrictModel):
    id: UUID
    namespace_id: UUID
    dataset_id: UUID
    state: str
    approval_hash: str
    specification: dict[str, JsonValue]
    selected_threshold: float | None
    catalog_entries: int
    report: dict[str, JsonValue]
    accounted_active_processing_us: int
    uncertain_active_processing_us: int
    held_active_processing_us: int
    expires_at: datetime


class SemanticCacheLookupReceipt(StrictModel):
    hit: bool
    reason: str
    response: str | None = None
    cache_id: UUID | None = None
    similarity: float | None = None
    lookup_processing_ms: float
    embedding_cost_usd: None = None
    complete_task_cost_usd: None = None
    realized_savings_usd: None = None


def enabled():
    if os.getenv("CUTBACK_SEMANTIC_CACHE_ENABLED", "false").lower() != "true":
        raise HTTPException(503, "SEMANTIC_CACHE_DISABLED")


def failure(exc, status=409):
    code = str(exc)
    if not re.fullmatch(r"[A-Z0-9_]{1,120}", code):
        code = "SEMANTIC_CACHE_CONTRACT_INVALID"
    return HTTPException(404 if code == "SEMANTIC_CACHE_NAMESPACE_NOT_FOUND" else status, code)


def namespace_receipt(session, row):
    count = (
        session.scalar(
            select(func.count())
            .select_from(SemanticCacheMember)
            .where(
                SemanticCacheMember.namespace_id == row.id,
                SemanticCacheMember.project_id == row.project_id,
            )
        )
        or 0
    )
    return {
        "id": str(row.id),
        "state": row.state,
        "specification": row.specification,
        "specification_hash": row.specification_hash,
        "embedding_contract_sha256": digest(row.specification["rule"]["embedding"]),
        "entry_count": count,
        "active_qualification_id": row.active_qualification_id,
        "active_approval_hash": row.active_approval_hash,
        "expires_at": row.expires_at,
    }


def qualification_receipt(row):
    return {
        "id": row.id,
        "namespace_id": row.namespace_id,
        "dataset_id": row.dataset_id,
        "state": row.state,
        "approval_hash": row.approval_hash,
        "specification": row.specification,
        "selected_threshold": row.selected_threshold,
        "catalog_entries": len(row.catalog_manifest),
        "report": row.report,
        "accounted_active_processing_us": row.active_elapsed_us - row.active_reserved_us,
        "uncertain_active_processing_us": row.active_uncertain_us,
        "held_active_processing_us": row.active_reserved_us,
        "expires_at": row.expires_at,
    }


def activation_hash(row, qualification):
    return digest(
        {
            "namespace_hash": row.specification_hash,
            "qualification_id": str(qualification.id),
            "qualification_hash": qualification.approval_hash,
            "catalog": qualification.catalog_manifest,
            "selected_threshold": qualification.selected_threshold,
            "qualification_report_hash": digest(qualification.report),
        }
    )


def validated_qualification(row, qualified, policy=None):
    if (
        not qualified
        or qualified.state != "PASSED"
        or qualified.expires_at <= datetime.now(UTC)
        or qualified.namespace_id != row.id
        or qualified.project_id != row.project_id
        or qualified.approval_hash != digest(qualified.specification)
        or qualified.specification["namespace_hash"] != row.specification_hash
        or qualified.selected_threshold is None
        or qualified.selected_threshold
        not in qualified.specification["qualification"]["thresholds"]
        or qualified.report.get("confirmation", {}).get("passed") is not True
        or qualified.specification.get("independent_labels_declared") is not True
    ):
        raise ValueError("SEMANTIC_CACHE_CURRENT_QUALIFICATION_DENIED")
    if policy is not None and digest(policy.model_dump(mode="json")) != qualified.specification.get(
        "capture_policy_hash"
    ):
        raise ValueError("SEMANTIC_CACHE_CAPTURE_POLICY_CHANGED_REAPPROVAL_REQUIRED")


def qualification(session, project, identifier):
    row = session.get(
        SemanticCacheQualification, identifier, with_for_update=True, populate_existing=True
    )
    if not row or row.project_id != project:
        raise HTTPException(404, "SEMANTIC_CACHE_QUALIFICATION_NOT_FOUND")
    return row


def install_semantic_cache(app, sessions, authorize, scope):
    @app.post(
        "/v1/projects/{project_id}/semantic-cache/namespaces",
        response_model=NamespaceReceipt,
        status_code=201,
    )
    def create(project_id: UUID, body: NamespaceInput, auth=Depends(authorize)):
        enabled()
        scope(auth, project_id, ("admin",))
        require_store(app)
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            environment = session.scalar(
                select(Environment).where(
                    Environment.project_id == project_id, Environment.name == body.environment
                )
            )
            key = session.get(AccessKey, body.serving_key_id)
            if (
                not environment
                or not key
                or key.project_id != project_id
                or key.environment_id != environment.id
                or key.role != "ingest"
            ):
                raise HTTPException(403, "SEMANTIC_CACHE_SERVING_KEY_OR_SCOPE_DENIED")
            now = datetime.now(UTC)
            if key.revoked_at or key.expires_at and key.expires_at <= now:
                raise HTTPException(403, "SEMANTIC_CACHE_SERVING_KEY_REVOKED_OR_EXPIRED")
            if (
                body.expires_at <= now
                or body.expires_at > now + timedelta(days=30)
                or key.expires_at
                and body.expires_at > key.expires_at
            ):
                raise HTTPException(422, "SEMANTIC_CACHE_EXPIRY_OUTSIDE_KEY_OR_TIME_BOUND")
            row = SemanticCacheNamespace(
                project_id=project_id,
                environment_id=environment.id,
                policy_id=body.policy_id,
                serving_key_id=body.serving_key_id,
                specification=body.model_dump(mode="json"),
                specification_hash=digest(body.model_dump(mode="json")),
                state="DRAFT",
                created_by=auth["actor"],
                expires_at=body.expires_at,
            )
            try:
                _, policy, _ = authority(session, row)
            except ValueError as exc:
                raise failure(exc, 403) from None
            if body.expires_at > policy.expires_at or body.expires_at > now + timedelta(
                days=policy.retention_days
            ):
                raise HTTPException(422, "SEMANTIC_CACHE_EXPIRY_EXCEEDS_CONTENT_RIGHTS")
            count = session.scalar(
                select(func.count())
                .select_from(SemanticCacheNamespace)
                .where(SemanticCacheNamespace.project_id == project_id)
            )
            if count >= 100:
                raise HTTPException(429, "SEMANTIC_CACHE_NAMESPACE_QUOTA_EXHAUSTED")
            session.add(row)
            session.flush()
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="semantic_cache_namespace_created",
                    details={
                        "namespace_id": str(row.id),
                        "specification_hash": row.specification_hash,
                    },
                )
            )
            return namespace_receipt(session, row)

    @app.get(
        "/v1/projects/{project_id}/semantic-cache/namespaces", response_model=list[NamespaceReceipt]
    )
    def namespaces(
        project_id: UUID,
        limit: int = Query(25, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1000),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            return [
                namespace_receipt(session, row)
                for row in session.scalars(
                    select(SemanticCacheNamespace)
                    .where(SemanticCacheNamespace.project_id == project_id)
                    .order_by(SemanticCacheNamespace.created_at.desc(), SemanticCacheNamespace.id)
                    .offset(offset)
                    .limit(limit)
                )
            ]

    @app.get("/v1/projects/{project_id}/semantic-cache/namespaces/{namespace_id}/entries")
    def entries(project_id: UUID, namespace_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id)
        with sessions() as session:
            try:
                row = namespace(session, project_id, namespace_id)
            except ValueError as exc:
                raise failure(exc) from None
            records = session.execute(
                select(SemanticCacheMember, CacheEntry)
                .join(
                    CacheEntry,
                    (SemanticCacheMember.cache_entry_id == CacheEntry.id)
                    & (SemanticCacheMember.project_id == CacheEntry.project_id),
                )
                .where(
                    SemanticCacheMember.namespace_id == row.id,
                    SemanticCacheMember.project_id == project_id,
                )
                .order_by(CacheEntry.id)
                .limit(256)
            ).all()
            return [
                {
                    "id": str(entry.id),
                    "content_hash": member.content_hash,
                    "expires_at": entry.expires_at,
                    "expired": entry.expires_at <= datetime.now(UTC),
                }
                for member, entry in records
            ]

    @app.post(
        "/v1/projects/{project_id}/semantic-cache/namespaces/{namespace_id}/entries/{entry_id}/content"
    )
    def entry_content(
        project_id: UUID,
        namespace_id: UUID,
        entry_id: UUID,
        body: ContentPurpose,
        auth=Depends(authorize),
    ):
        enabled()
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            try:
                row = namespace(session, project_id, namespace_id)
                _, policy, _ = authority(session, row)
                record = next(
                    (
                        (member, entry)
                        for member, entry in catalog(session, row)
                        if entry.id == entry_id
                    ),
                    None,
                )
                if record is None:
                    raise HTTPException(404, "SEMANTIC_CACHE_ENTRY_NOT_FOUND")
                member, entry = record
                raw = json.loads(require_store(app).get(project_id, entry.object_ref))
                if digest(raw) != member.content_hash:
                    raise ValueError("SEMANTIC_CACHE_CATALOG_CONTENT_INTEGRITY_FAILURE")
                policy_content(raw, policy, preserve_semantics=True)
            except ValueError as exc:
                raise failure(exc) from None
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="semantic_cache_content_reviewed",
                    details={
                        "namespace_id": str(row.id),
                        "cache_entry_id": str(entry.id),
                        "purpose": body.purpose,
                    },
                )
            )
            return {
                "query": raw["query"],
                "response": raw["response"],
                "vector": raw["vector"],
                "source_group": raw.get("source_group"),
                "content": "explicit_purpose_authorized",
            }

    @app.delete("/v1/projects/{project_id}/semantic-cache/namespaces/{namespace_id}")
    def delete_namespace(
        project_id: UUID, namespace_id: UUID, body: SemanticApproval, auth=Depends(authorize)
    ):
        scope(auth, project_id, ("admin",))
        store = require_store(app)
        from cutback.server.semantic_upload_db import SemanticDatasetUpload, SemanticUploadCase

        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = namespace(session, project_id, namespace_id, lock=True)
            if body.approval_hash != row.specification_hash:
                raise HTTPException(409, "SEMANTIC_CACHE_DELETE_EXACT_HASH_REQUIRED")
            row.state, row.active_qualification_id, row.active_approval_hash = "REVOKED", None, None
            session.flush()
            # Finalized datasets remain independently retained. Only unassigned
            # staging content belongs exclusively to the namespace being deleted.
            upload_ids = select(SemanticDatasetUpload.id).where(
                SemanticDatasetUpload.project_id == project_id,
                SemanticDatasetUpload.namespace_id == namespace_id,
            )
            staged_ids = select(SemanticUploadCase.payload_id).where(
                SemanticUploadCase.project_id == project_id,
                SemanticUploadCase.upload_id.in_(upload_ids),
            )
            unused = session.scalars(
                select(PayloadSample).where(
                    PayloadSample.project_id == project_id,
                    PayloadSample.id.in_(staged_ids),
                    ~select(DatasetItem.id)
                    .where(DatasetItem.payload_id == PayloadSample.id)
                    .exists(),
                )
            ).all()
            cache_entries = session.scalars(
                select(CacheEntry)
                .join(
                    SemanticCacheMember,
                    (SemanticCacheMember.cache_entry_id == CacheEntry.id)
                    & (SemanticCacheMember.project_id == CacheEntry.project_id),
                )
                .where(
                    SemanticCacheMember.namespace_id == namespace_id,
                    CacheEntry.project_id == project_id,
                )
            ).all()
            for item in [*unused, *cache_entries]:
                store.delete(project_id, item.object_ref)
            # Cascades remove memberships, upload manifests and qualifications
            # before their restrictive encrypted-payload pointers are removed.
            session.execute(
                delete(SemanticCacheNamespace).where(
                    SemanticCacheNamespace.id == namespace_id,
                    SemanticCacheNamespace.project_id == project_id,
                )
            )
            for item in [*unused, *cache_entries]:
                session.delete(item)
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="semantic_cache_namespace_deleted",
                    details={
                        "namespace_id": str(namespace_id),
                        "approval_ref": body.approval_ref,
                        "finalized_datasets_retained": True,
                    },
                )
            )
        return {
            "status": "deleted",
            "namespace_id": str(namespace_id),
            "finalized_datasets_retained": True,
        }

    @app.get(
        "/v1/projects/{project_id}/semantic-cache/namespaces/{namespace_id}",
        response_model=NamespaceReceipt,
    )
    def inspect_namespace(project_id: UUID, namespace_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id)
        with sessions() as session:
            try:
                return namespace_receipt(session, namespace(session, project_id, namespace_id))
            except ValueError as exc:
                raise failure(exc) from None

    @app.post(
        "/v1/projects/{project_id}/semantic-cache/namespaces/{namespace_id}/revoke",
        response_model=NamespaceReceipt,
    )
    def revoke(
        project_id: UUID, namespace_id: UUID, body: SemanticControl, auth=Depends(authorize)
    ):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            try:
                row = namespace(session, project_id, namespace_id, lock=True)
            except ValueError as exc:
                raise failure(exc) from None
            row.state, row.active_qualification_id, row.active_approval_hash = "REVOKED", None, None
            for pending in session.scalars(
                select(SemanticCacheQualification).where(
                    SemanticCacheQualification.namespace_id == row.id,
                    SemanticCacheQualification.state.in_(
                        ["PENDING", "QUEUED", "CALIBRATING", "CALIBRATED", "CONFIRMING"]
                    ),
                )
            ):
                pending.state = "CANCELLED"
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="semantic_cache_namespace_revoked",
                    details={"namespace_id": str(row.id)},
                )
            )
            return namespace_receipt(session, row)

    @app.post("/v1/ingest/semantic-cache/put", status_code=201)
    def put(body: SemanticPut, auth=Depends(authorize)):
        enabled()
        scope(auth, body.project_id, ("ingest",))
        if auth["role"] != "ingest":
            raise HTTPException(403, "SEMANTIC_CACHE_ASSIGNED_INGEST_KEY_REQUIRED")
        store = require_store(app)
        try:
            with content_transaction(sessions, store, body.project_id) as (session, refs):
                lock_lineage(session, body.project_id)
                row = namespace(session, body.project_id, body.namespace_id, lock=True)
                spec, policy, key = authority(session, row, serving_actor=auth["actor"])
                if (
                    body.access_context_sha256 != spec.access_context_sha256
                    or body.embedding_contract_sha256
                    != digest(spec.rule.embedding.model_dump(mode="json"))
                ):
                    raise ValueError("SEMANTIC_CACHE_REQUEST_CONTEXT_OR_EMBEDDING_MISMATCH")
                unit_vector(body.vector, spec.rule.embedding.dimensions)
                if row.state != "DRAFT":
                    raise ValueError("SEMANTIC_CACHE_CURATED_CATALOG_IS_IMMUTABLE_AFTER_ACTIVATION")
                if session.scalar(
                    select(SemanticCacheQualification.id)
                    .where(
                        SemanticCacheQualification.namespace_id == row.id,
                        SemanticCacheQualification.state.in_(
                            ["QUEUED", "CALIBRATING", "CALIBRATED", "CONFIRMING"]
                        ),
                    )
                    .limit(1)
                ):
                    raise ValueError("SEMANTIC_CACHE_QUALIFICATION_IN_PROGRESS")
                session.get(
                    CapturePolicyRecord, row.policy_id, with_for_update=True, populate_existing=True
                )
                if policy_object_count(session, row.policy_id) >= policy.max_samples:
                    raise HTTPException(429, "CAPTURE_POLICY_QUOTA_EXHAUSTED")
                current = catalog(session, row)
                if len(current) >= spec.max_entries:
                    raise HTTPException(429, "SEMANTIC_CACHE_CATALOG_QUOTA_EXHAUSTED")
                keyed = query_key(row, key, body.query)
                if session.scalar(
                    select(CacheEntry.id)
                    .where(CacheEntry.project_id == body.project_id, CacheEntry.key_hash == keyed)
                    .limit(1)
                ):
                    raise ValueError(
                        "SEMANTIC_CACHE_ENTRY_IMMUTABLE_INVALIDATE_OR_CHANGE_NAMESPACE"
                    )
                if body.ttl_seconds > policy.retention_days * 86400:
                    raise ValueError("SEMANTIC_CACHE_TTL_EXCEEDS_CONTENT_POLICY")
                identifier = uuid4()
                raw = {
                    "nonce": str(uuid4()),
                    "namespace_id": str(row.id),
                    "namespace_hash": row.specification_hash,
                    "cache_id": str(identifier),
                    "key_hash": keyed,
                    "query": body.query,
                    "response": body.response,
                    "vector": list(body.vector),
                    "source_group": body.source_group,
                }
                _, blob = policy_content(raw, policy, preserve_semantics=True)
                family = register_groups(
                    session,
                    body.project_id,
                    [
                        Case(
                            id="semantic-" + str(identifier),
                            input=body.query,
                            group=body.source_group,
                        )
                    ],
                    actor=auth["actor"],
                )
                require_unreserved_groups(session, body.project_id, set(family.values()))
                ref = store.put(body.project_id, blob, max(1, (body.ttl_seconds + 86399) // 86400))
                refs.append(ref)
                expiry = min(
                    datetime.now(UTC) + timedelta(seconds=body.ttl_seconds),
                    row.expires_at,
                    policy.expires_at,
                )
                entry = CacheEntry(
                    id=identifier,
                    project_id=body.project_id,
                    policy_id=row.policy_id,
                    key_hash=keyed,
                    object_ref=ref,
                    expires_at=expiry,
                )
                session.add(entry)
                session.flush()
                session.add(
                    SemanticCacheMember(
                        cache_entry_id=identifier,
                        project_id=body.project_id,
                        namespace_id=row.id,
                        content_hash=digest(raw),
                    )
                )
                session.add(
                    DatasetExposure(
                        project_id=body.project_id,
                        family_id=next(iter(family.values())),
                        source_ref="semantic-anchor:" + str(identifier),
                        mode="train",
                        protected=False,
                    )
                )
                session.add(
                    AuditEvent(
                        project_id=body.project_id,
                        actor=auth["actor"],
                        action="semantic_cache_entry_created",
                        details={
                            "namespace_id": str(row.id),
                            "cache_id": str(identifier),
                            "bytes": len(blob),
                        },
                    )
                )
                return {
                    "id": str(identifier),
                    "expires_at": expiry.isoformat(),
                    "content": "encrypted",
                    "namespace_id": str(row.id),
                }
        except ValueError as exc:
            raise failure(exc) from None

    @app.post("/v1/ingest/semantic-cache/lookup", response_model=SemanticCacheLookupReceipt)
    def lookup(body: SemanticLookup, auth=Depends(authorize)):
        enabled()
        scope(auth, body.project_id, ("ingest",))
        if auth["role"] != "ingest":
            raise HTTPException(403, "SEMANTIC_CACHE_ASSIGNED_INGEST_KEY_REQUIRED")
        started = monotonic()

        def bounded():
            if monotonic() - started >= 5:
                raise ValueError("SEMANTIC_CACHE_LOOKUP_TIME_BOUND")

        try:
            with sessions() as session:
                row = namespace(session, body.project_id, body.namespace_id)
                spec, policy, _ = authority(session, row, serving_actor=auth["actor"])
                if (
                    body.access_context_sha256 != spec.access_context_sha256
                    or body.embedding_contract_sha256
                    != digest(spec.rule.embedding.model_dump(mode="json"))
                ):
                    raise ValueError("SEMANTIC_CACHE_REQUEST_CONTEXT_OR_EMBEDDING_MISMATCH")
                unit_vector(body.vector, spec.rule.embedding.dimensions)
                if row.state != "ACTIVE" or not row.active_qualification_id:
                    raise ValueError("SEMANTIC_CACHE_QUALIFIED_ACTIVATION_REQUIRED")
                qualified = session.get(SemanticCacheQualification, row.active_qualification_id)
                validated_qualification(row, qualified, policy)
                if row.active_approval_hash != activation_hash(row, qualified):
                    raise ValueError("SEMANTIC_CACHE_ACTIVATED_EVIDENCE_CHANGED")
                if (
                    not qualified
                    or qualified.state != "PASSED"
                    or qualified.expires_at <= datetime.now(UTC)
                    or principal_role(session, row.project_id, qualified.approved_by or "")
                    not in {"admin", "project_admin"}
                ):
                    raise ValueError("SEMANTIC_CACHE_CURRENT_QUALIFICATION_DENIED")
                from cutback.server.operations import enforce_quota

                enforce_quota(sessions, "semantic-project:" + str(row.project_id), 120)
                enforce_quota(
                    sessions, "semantic-namespace:" + str(row.id), spec.max_lookups_per_minute
                )
                anchors, _, _ = read_catalog(
                    session,
                    require_store(app),
                    row,
                    expected=qualified.catalog_manifest,
                    check=bounded,
                )
                selected = spec.rule.model_copy(update={"threshold": qualified.selected_threshold})
                match = SemanticCacheIndex(selected, anchors, check=bounded).lookup(body.vector)
                authority(session, row, serving_actor=auth["actor"])
                bounded()
                return {
                    "hit": match.hit,
                    "reason": match.code,
                    "response": match.response,
                    "cache_id": match.anchor_id if match.hit else None,
                    "similarity": match.score,
                    "lookup_processing_ms": (monotonic() - started) * 1000,
                }
        except ValueError as exc:
            code = str(exc)
            if not re.fullmatch(r"[A-Z0-9_]{1,120}", code):
                code = "SEMANTIC_CACHE_LOOKUP_UNAVAILABLE"
            return {
                "hit": False,
                "reason": code,
                "lookup_processing_ms": (monotonic() - started) * 1000,
            }
        except (OSError, SQLAlchemyError):
            return {
                "hit": False,
                "reason": "SEMANTIC_CACHE_LOOKUP_UNAVAILABLE",
                "lookup_processing_ms": (monotonic() - started) * 1000,
            }

    @app.post(
        "/v1/projects/{project_id}/semantic-cache/namespaces/{namespace_id}/qualifications",
        response_model=QualificationReceipt,
        status_code=201,
    )
    def prepare(
        project_id: UUID, namespace_id: UUID, body: QualificationPrepare, auth=Depends(authorize)
    ):
        enabled()
        scope(auth, project_id, ("analyst",))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            try:
                row = namespace(session, project_id, namespace_id, lock=True)
                _, policy, _ = authority(session, row)
                cases, dataset, loaded = qualification_queries(
                    session,
                    require_store(app),
                    row,
                    body.dataset_id,
                    body.specification,
                    actor=auth["actor"],
                    register=True,
                )
                records = catalog(session, row)
                if not records:
                    raise ValueError("SEMANTIC_CACHE_NONEMPTY_CATALOG_REQUIRED")
                inspect_exposure(session, project_id, dataset.id, "test", require_fresh=True)
            except ValueError as exc:
                raise failure(exc) from None
            count = session.scalar(
                select(func.count())
                .select_from(SemanticCacheQualification)
                .where(SemanticCacheQualification.project_id == project_id)
            )
            if count >= 100:
                raise HTTPException(429, "SEMANTIC_CACHE_QUALIFICATION_QUOTA_EXHAUSTED")
            frozen = {
                "namespace_hash": row.specification_hash,
                "dataset_hash": dataset.immutable_hash,
                "dataset_id": str(dataset.id),
                "qualification": body.specification.model_dump(mode="json"),
                "max_wall_seconds": body.max_wall_seconds,
                "validation_cases": sum(case.split == "validation" for case in cases),
                "test_cases": sum(case.split == "test" for case in cases),
                "independent_labels_declared": dataset.provenance.get("independent_labels") is True,
                "evidence_origin": dataset.provenance["source_type"],
                "billable_calls": 0,
                "catalog_hash": digest(manifest(records)),
                "capture_policy_hash": digest(policy.model_dump(mode="json")),
            }
            receipt = SemanticCacheQualification(
                project_id=project_id,
                namespace_id=row.id,
                dataset_id=dataset.id,
                specification=frozen,
                approval_hash=digest(frozen),
                state="PENDING",
                catalog_manifest=manifest(records),
                expires_at=min(
                    row.expires_at,
                    datetime.fromisoformat(loaded["expires_at"]),
                    *(entry.expires_at for _, entry in records),
                ),
            )
            session.add(receipt)
            session.flush()
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="semantic_cache_qualification_prepared",
                    details={
                        "qualification_id": str(receipt.id),
                        "approval_hash": receipt.approval_hash,
                    },
                )
            )
            return qualification_receipt(receipt)

    @app.get(
        "/v1/projects/{project_id}/semantic-cache/qualifications",
        response_model=list[QualificationReceipt],
    )
    def qualifications(
        project_id: UUID,
        limit: int = Query(25, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1000),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            return [
                qualification_receipt(row)
                for row in session.scalars(
                    select(SemanticCacheQualification)
                    .where(SemanticCacheQualification.project_id == project_id)
                    .order_by(
                        SemanticCacheQualification.created_at.desc(), SemanticCacheQualification.id
                    )
                    .offset(offset)
                    .limit(limit)
                )
            ]

    @app.get(
        "/v1/projects/{project_id}/semantic-cache/qualifications/{qualification_id}",
        response_model=QualificationReceipt,
    )
    def inspect_qualification(project_id: UUID, qualification_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id)
        with sessions() as session:
            return qualification_receipt(qualification(session, project_id, qualification_id))

    @app.post(
        "/v1/projects/{project_id}/semantic-cache/qualifications/{qualification_id}/approve",
        response_model=QualificationReceipt,
        status_code=202,
    )
    def approve(
        project_id: UUID, qualification_id: UUID, body: SemanticApproval, auth=Depends(authorize)
    ):
        enabled()
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = qualification(session, project_id, qualification_id)
            ns = namespace(session, project_id, row.namespace_id, lock=True)
            if (
                row.state != "PENDING"
                or row.approval_hash != body.approval_hash
                or row.approval_hash != digest(row.specification)
                or row.expires_at <= datetime.now(UTC)
            ):
                raise HTTPException(409, "SEMANTIC_CACHE_APPROVAL_MISMATCH_EXPIRED_OR_CONSUMED")
            try:
                authority(session, ns)
                if digest(manifest(catalog(session, ns))) != row.specification["catalog_hash"]:
                    raise ValueError("SEMANTIC_CACHE_CATALOG_CHANGED_REQUALIFY")
            except ValueError as exc:
                raise failure(exc) from None
            row.state, row.approved_by = "QUEUED", auth["actor"]
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="semantic_cache_qualification_approved",
                    details={"qualification_id": str(row.id), "approval_ref": body.approval_ref},
                )
            )
            return qualification_receipt(row)

    @app.post(
        "/v1/projects/{project_id}/semantic-cache/qualifications/{qualification_id}/cancel",
        response_model=QualificationReceipt,
    )
    def cancel(
        project_id: UUID, qualification_id: UUID, body: SemanticControl, auth=Depends(authorize)
    ):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = qualification(session, project_id, qualification_id)
            if row.state not in {"PASSED", "FAILED", "EXPIRED"}:
                row.state = "CANCELLED"
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="semantic_cache_qualification_cancelled",
                    details={"qualification_id": str(row.id)},
                )
            )
            return qualification_receipt(row)

    @app.get(
        "/v1/projects/{project_id}/semantic-cache/qualifications/{qualification_id}/activation"
    )
    def activation(project_id: UUID, qualification_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id)
        with sessions() as session:
            row = qualification(session, project_id, qualification_id)
            ns = namespace(session, project_id, row.namespace_id)
            reason = None
            try:
                _, policy, _ = authority(session, ns)
                validated_qualification(ns, row, policy)
                if ns.state != "DRAFT":
                    raise ValueError("SEMANTIC_CACHE_DRAFT_NAMESPACE_REQUIRED")
                if manifest(catalog(session, ns)) != row.catalog_manifest:
                    raise ValueError("SEMANTIC_CACHE_CATALOG_CHANGED_REQUALIFY")
                if principal_role(session, project_id, row.approved_by or "") not in {
                    "admin",
                    "project_admin",
                }:
                    raise ValueError("SEMANTIC_CACHE_QUALIFICATION_APPROVER_REVOKED")
                inspect_exposure(
                    session,
                    project_id,
                    row.dataset_id,
                    "test",
                    source_ref="semantic-qualification:" + str(row.id) + ":test",
                    require_fresh=True,
                )
            except ValueError as exc:
                reason = str(failure(exc).detail)
            return {
                "qualification_id": str(row.id),
                "namespace_id": str(ns.id),
                "activation_hash": activation_hash(ns, row),
                "eligible": reason is None,
                "ineligibility_reason": reason,
                "selected_threshold": row.selected_threshold,
                "scope": "curated_frozen_catalog_only",
                "realized_savings_usd": None,
            }

    @app.post(
        "/v1/projects/{project_id}/semantic-cache/qualifications/{qualification_id}/activate",
        response_model=NamespaceReceipt,
    )
    def activate(
        project_id: UUID, qualification_id: UUID, body: SemanticApproval, auth=Depends(authorize)
    ):
        enabled()
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = qualification(session, project_id, qualification_id)
            ns = namespace(session, project_id, row.namespace_id, lock=True)
            if (
                row.state != "PASSED"
                or body.approval_hash != activation_hash(ns, row)
                or ns.state != "DRAFT"
                or row.expires_at <= datetime.now(UTC)
                or not row.specification["independent_labels_declared"]
            ):
                raise HTTPException(409, "SEMANTIC_CACHE_QUALIFIED_EXACT_ACTIVATION_REQUIRED")
            try:
                _, policy, _ = authority(session, ns)
                if manifest(catalog(session, ns)) != row.catalog_manifest:
                    raise ValueError("SEMANTIC_CACHE_CATALOG_CHANGED_REQUALIFY")
                inspect_exposure(
                    session,
                    project_id,
                    row.dataset_id,
                    "test",
                    source_ref="semantic-qualification:" + str(row.id) + ":test",
                    require_fresh=True,
                )
            except ValueError as exc:
                raise failure(exc) from None
            if principal_role(session, project_id, row.approved_by or "") not in {
                "admin",
                "project_admin",
            }:
                raise HTTPException(403, "SEMANTIC_CACHE_QUALIFICATION_APPROVER_REVOKED")
            try:
                validated_qualification(ns, row, policy)
            except ValueError as exc:
                raise failure(exc) from None
            ns.state, ns.active_qualification_id, ns.active_approval_hash = (
                "ACTIVE",
                row.id,
                body.approval_hash,
            )
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="semantic_cache_qualified_namespace_activated",
                    details={
                        "namespace_id": str(ns.id),
                        "qualification_id": str(row.id),
                        "activation_hash": body.approval_hash,
                        "approval_ref": body.approval_ref,
                    },
                )
            )
            return namespace_receipt(session, ns)
