"""Frozen blind review, expiring leases and immutable decisions. No inference or code execution."""

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from fastapi import Depends, HTTPException, Query
from pydantic import Field, JsonValue, StrictBool, model_validator
from sqlalchemy import select

from cutback.core.models import Identifier, StrictModel
from cutback.evals.datasets import Case, DatasetImport, digest
from cutback.server.content_policy import policy_content
from cutback.server.dataset_lineage import (
    LineageViolation,
    lock_lineage,
    register_groups,
    require_unreserved_groups,
)
from cutback.server.db import (
    AccessKey,
    AuditEvent,
    DatasetItem,
    DatasetRecord,
    PayloadSample,
    Project,
)
from cutback.server.enterprise_db import OrganizationMember
from cutback.server.evaluation_api import (
    ContentPurpose,
    active_policy,
    persist_dataset,
    policy_object_count,
    require_store,
)
from cutback.server.lineage_db import DatasetExposure
from cutback.server.review_db import (
    ReviewItem,
    ReviewLease,
    ReviewQueue,
    ReviewResolution,
    ReviewVote,
)
from cutback.server.trace_db import TraceContent


class ReviewSource(StrictModel):
    kind: Literal["dataset_item", "trace_content"]
    id: UUID


class ReviewQueueInput(StrictModel):
    policy_id: UUID
    workflow: Identifier
    environment: Identifier
    sources: list[ReviewSource] = Field(min_length=1, max_length=200)
    rubric: str = Field(min_length=1, max_length=16000)
    reviewers: list[str] = Field(min_length=2, max_length=20)
    adjudicator: str = Field(min_length=1, max_length=64)
    required_reviewers: int = Field(default=2, ge=2, le=5)
    mode: Literal["labeling", "calibration"] = "labeling"
    provenance_ref: Identifier

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.reviewers)) != len(self.reviewers) or self.adjudicator in self.reviewers:
            raise ValueError("DISTINCT_REVIEWERS_AND_ADJUDICATOR_REQUIRED")
        if self.required_reviewers > len(self.reviewers) or len(
            {(s.kind, s.id) for s in self.sources}
        ) != len(self.sources):
            raise ValueError("REVIEW_QUORUM_OR_DUPLICATE_SOURCE_INVALID")
        if any(len(actor) > 64 for actor in self.reviewers):
            raise ValueError("INVALID_REVIEW_PRINCIPAL")
        return self


class ReviewClaim(StrictModel):
    lease_seconds: int = Field(default=900, ge=30, le=3600)


class ReviewRelease(ContentPurpose):
    lease_id: UUID


class ReviewJudgment(StrictModel):
    decision: Literal["label", "exclude"]
    gold: str | list[str] | dict[str, JsonValue] | None = None
    rationale: str = Field(min_length=1, max_length=8000)

    @model_validator(mode="after")
    def label_required(self):
        if (self.decision == "label") != (self.gold is not None):
            raise ValueError("LABEL_REQUIRES_GOLD_EXCLUSION_FORBIDS_GOLD")
        return self


class ReviewSubmission(ReviewJudgment):
    lease_id: UUID
    queue_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReviewAdjudication(ReviewJudgment):
    queue_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReviewPromotion(StrictModel):
    queue_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    provenance_ref: Identifier
    rights_confirmed: StrictBool
    independent_reviewers_attested: StrictBool


class ReviewClose(ContentPurpose):
    queue_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


def principal_active(session, project, actor):
    from cutback.server.principals import principal_is_active

    return principal_is_active(
        session, project, actor, roles={"analyst", "editor", "project_admin", "admin"}
    )


def _payload_access(session, project, payload_id, *, workflow, environment):
    row = session.get(PayloadSample, payload_id)
    if (
        not row
        or row.project_id != project
        or row.expires_at <= datetime.now(UTC)
        or row.metadata_safe.get("deleted")
    ):
        raise HTTPException(410, "REVIEW_CONTENT_EXPIRED_OR_UNAVAILABLE")
    _, policy = active_policy(session, project, row.policy_id)
    if not policy.permits(environment, workflow):
        raise HTTPException(403, "REVIEW_CONTENT_SCOPE_DENIED")
    provider = row.metadata_safe.get("provider")
    if provider and provider not in policy.allowed_providers:
        raise HTTPException(403, "REVIEW_CONTENT_PROVIDER_SCOPE_DENIED")
    return row, policy


def _read(session, store, project, payload_id, *, workflow, environment):
    row, policy = _payload_access(
        session, project, payload_id, workflow=workflow, environment=environment
    )
    try:
        value = json.loads(store.get(project, row.object_ref))
        if digest(value) != row.content_hash:
            raise ValueError("INTEGRITY_FAILURE")
        policy_content(value, policy, preserve_semantics=True)
    except (OSError, ValueError, InvalidTag):
        raise HTTPException(410, "REVIEW_CONTENT_EXPIRED_OR_UNAVAILABLE") from None
    return value


def _write(session, store, project, policy_id, policy, value, refs, *, kind):
    if policy_object_count(session, policy_id) >= policy.max_samples:
        raise HTTPException(429, "CAPTURE_SAMPLE_QUOTA_EXCEEDED")
    # Random nonce prevents dictionary attacks against low-cardinality decisions in metadata export.
    value = {**value, "receipt_nonce": uuid4().hex}
    try:
        clean, blob = policy_content(value, policy, preserve_semantics=True)
    except ValueError:
        raise HTTPException(422, "REVIEW_CONTENT_REQUIRES_APPROVED_DEIDENTIFIED_BYTES") from None
    ref = store.put(project, blob, policy.retention_days)
    refs.append(ref)
    row = PayloadSample(
        project_id=project,
        policy_id=policy_id,
        object_ref=ref,
        content_hash=digest(clean),
        expires_at=min(
            policy.expires_at, datetime.now(UTC) + timedelta(days=policy.retention_days)
        ),
        metadata_safe={"kind": kind},
    )
    session.add(row)
    session.flush()
    return row


def _audit(session, project, actor, action, **details):
    session.add(AuditEvent(project_id=project, actor=actor, action=action, details=details))


def _summary(row):
    return {
        "id": str(row.id),
        "version_hash": row.version_hash,
        "workflow": row.workflow,
        "environment": row.environment,
        "mode": row.mode,
        "status": row.status,
        "required_reviewers": row.required_reviewers,
        "created_at": row.created_at.isoformat(),
        "dataset_id": str(row.promoted_dataset_id) if row.promoted_dataset_id else None,
    }


def install_reviews(app, sessions, authorize, scope):
    @app.get("/v1/projects/{project_id}/reviews/principals")
    def principals(project_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with sessions() as session:
            project = session.get(Project, project_id)
            if not project:
                raise HTTPException(404, "PROJECT_NOT_FOUND")
            keys = session.scalars(
                select(AccessKey)
                .where(
                    AccessKey.project_id == project_id,
                    AccessKey.revoked_at.is_(None),
                    AccessKey.role.in_(["analyst", "editor", "project_admin"]),
                )
                .order_by(AccessKey.id)
                .limit(201)
            ).all()
            members = session.scalars(
                select(OrganizationMember)
                .where(
                    OrganizationMember.organization_id == project.organization_id,
                    OrganizationMember.enabled.is_(True),
                )
                .order_by(OrganizationMember.id)
                .limit(201)
            ).all()
            actors = [
                {
                    "actor": str(k.id),
                    "label": k.role.replace("_", " ") + " key " + str(k.id)[:8],
                    "can_review": True,
                }
                for k in keys[:200]
                if principal_active(session, project_id, str(k.id))
            ]
            for member in members[:200]:
                actor = "oidc:" + member.subject_hash[:32]
                if principal_active(session, project_id, actor):
                    actors.append(
                        {
                            "actor": actor,
                            "label": "OIDC member " + str(member.id)[:8],
                            "can_review": True,
                        }
                    )
            if auth["actor"] == "bootstrap_admin":
                actors.insert(
                    0,
                    {
                        "actor": "bootstrap_admin",
                        "label": "Installation administrator",
                        "can_review": False,
                    },
                )
            return {
                "items": actors,
                "truncated": len(keys) > 200 or len(members) > 200,
                "current_actor": auth["actor"],
            }

    @app.post("/v1/projects/{project_id}/reviews/{queue_id}/close")
    def close_queue(project_id: UUID, queue_id: UUID, body: ReviewClose, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            q = session.get(ReviewQueue, queue_id, with_for_update=True)
            if not q or q.project_id != project_id:
                raise HTTPException(404, "REVIEW_QUEUE_NOT_FOUND")
            if q.version_hash != body.queue_hash:
                raise HTTPException(409, "REVIEW_QUEUE_VERSION_CHANGED")
            if q.status != "open":
                return {"status": q.status}
            q.status = "closed"
            items = session.scalars(select(ReviewItem).where(ReviewItem.queue_id == q.id)).all()
            for r in items:
                for lease in session.scalars(
                    select(ReviewLease).where(
                        ReviewLease.item_id == r.id, ReviewLease.status == "active"
                    )
                ):
                    lease.status = "released"
                if r.state == "leased":
                    voted = session.scalar(
                        select(ReviewVote.id).where(ReviewVote.item_id == r.id).limit(1)
                    )
                    r.state = "reviewed" if voted else "ready"
            _audit(
                session,
                project_id,
                auth["actor"],
                "review_queue_closed",
                queue_id=str(q.id),
                purpose=body.purpose,
            )
            return {"status": "closed", "judgments_deleted": False}

    @app.get("/v1/projects/{project_id}/review-sources")
    def source_items(
        project_id: UUID,
        dataset_id: UUID | None = None,
        trace_content_id: UUID | None = None,
        after: UUID | None = None,
        limit: int = Query(100, ge=1, le=200),
        auth=Depends(authorize),
    ):
        scope(auth, project_id, ("analyst",))
        if (dataset_id is None) == (trace_content_id is None):
            raise HTTPException(422, "SELECT_ONE_DATASET_OR_TRACE_CONTENT_SOURCE")
        with sessions() as session:
            if trace_content_id:
                link = session.get(TraceContent, trace_content_id)
                if not link or link.project_id != project_id:
                    raise HTTPException(404, "REVIEW_SOURCE_NOT_FOUND")
                payload = session.get(PayloadSample, link.payload_id)
                return {
                    "items": [
                        {
                            "id": str(link.id),
                            "kind": "trace_content",
                            "split": "captured_trace",
                            "lineage_known": False,
                        }
                    ],
                    "workflow": payload.metadata_safe["workflow"],
                    "environment": payload.metadata_safe["environment"],
                    "next_cursor": None,
                    "content": "metadata_only",
                    "warning": "selected_groups_become_development_evidence",
                }
            dataset = session.get(DatasetRecord, dataset_id)
            if not dataset or dataset.project_id != project_id:
                raise HTTPException(404, "DATASET_NOT_FOUND")
            query = select(DatasetItem).where(DatasetItem.dataset_id == dataset_id)
            if after:
                query = query.where(DatasetItem.id > after)
            rows = session.scalars(query.order_by(DatasetItem.id).limit(limit + 1)).all()
            return {
                "items": [
                    {
                        "id": str(r.id),
                        "kind": "dataset_item",
                        "split": r.split,
                        "lineage_known": r.lineage_group_id is not None,
                    }
                    for r in rows[:limit]
                ],
                "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
                "workflow": dataset.workflow_id,
                "environment": dataset.provenance["environment"],
                "content": "metadata_only",
                "warning": "selected_groups_become_development_evidence",
            }

    @contextmanager
    def transaction(project):
        store = require_store(app)
        refs: list[str] = []
        try:
            with sessions.begin() as session:
                # One lock order across imports, review, promotion and evaluation reservations.
                lock_lineage(session, project)
                yield session, store, refs
        except BaseException:
            for ref in refs:
                store.delete(project, ref)
            raise

    def queue(session, store, project, identifier):
        row = session.get(ReviewQueue, identifier, with_for_update=True)
        if not row or row.project_id != project:
            raise HTTPException(404, "REVIEW_QUEUE_NOT_FOUND")
        config = _read(
            session,
            store,
            project,
            row.payload_id,
            workflow=row.workflow,
            environment=row.environment,
        )
        if digest({k: v for k, v in config.items() if k != "receipt_nonce"}) != row.version_hash:
            raise HTTPException(409, "REVIEW_QUEUE_INTEGRITY_FAILURE")
        for payload_id in config["source_payload_ids"]:
            _payload_access(
                session,
                project,
                UUID(payload_id),
                workflow=row.workflow,
                environment=row.environment,
            )
        _, policy = active_policy(session, project, row.policy_id)
        return row, config, policy

    def item(session, project, q, identifier):
        row = session.get(ReviewItem, identifier, with_for_update=True)
        if not row or row.project_id != project or row.queue_id != q.id:
            raise HTTPException(404, "REVIEW_ITEM_NOT_FOUND")
        return row

    def read(session, store, q, payload):
        return _read(
            session, store, q.project_id, payload, workflow=q.workflow, environment=q.environment
        )

    def eligible(session, project, config, actor, *, adjudicator=False):
        allowed = actor == config["adjudicator"] if adjudicator else actor in config["reviewers"]
        if not allowed or not principal_active(session, project, actor):
            raise HTTPException(403, "CURRENT_REVIEW_ASSIGNMENT_REQUIRED")

    def expire_leases(session, row):
        now = datetime.now(UTC)
        leases = session.scalars(
            select(ReviewLease).where(ReviewLease.item_id == row.id, ReviewLease.status == "active")
        ).all()
        live = []
        for lease in leases:
            if lease.expires_at <= now or not principal_active(
                session, row.project_id, lease.actor
            ):
                lease.status = "expired"
            else:
                live.append(lease)
        votes = session.scalars(select(ReviewVote).where(ReviewVote.item_id == row.id)).all()
        if row.state in {"ready", "leased", "reviewed"}:
            row.state = "leased" if live else "reviewed" if votes else "ready"
        return live, votes

    @app.post("/v1/projects/{project_id}/reviews", status_code=201)
    def create(project_id: UUID, body: ReviewQueueInput, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with transaction(project_id) as (session, store, refs):
            if any(
                actor == "bootstrap_admin" or not principal_active(session, project_id, actor)
                for actor in body.reviewers
            ):
                raise HTTPException(422, "REVIEWERS_REQUIRE_DISTINCT_CURRENT_SCOPED_CREDENTIALS")
            if not principal_active(session, project_id, body.adjudicator):
                raise HTTPException(422, "ADJUDICATOR_GRANT_REQUIRED")
            _, policy = active_policy(session, project_id, body.policy_id)
            if not policy.permits(body.environment, body.workflow):
                raise HTTPException(403, "CAPTURE_SCOPE_DENIED")
            # Source policies are locked in UUID order to preserve a single lock order.
            sources = []
            for source in body.sources:
                if source.kind == "dataset_item":
                    source_item = session.get(DatasetItem, source.id)
                    dataset = (
                        session.get(DatasetRecord, source_item.dataset_id) if source_item else None
                    )
                    if not dataset or dataset.project_id != project_id:
                        raise HTTPException(404, "REVIEW_SOURCE_NOT_FOUND")
                    if (
                        dataset.workflow_id != body.workflow
                        or dataset.provenance["environment"] != body.environment
                    ):
                        raise HTTPException(403, "REVIEW_SOURCE_SCOPE_DENIED")
                    payload_id = source_item.payload_id
                else:
                    link = session.get(TraceContent, source.id)
                    if not link or link.project_id != project_id:
                        raise HTTPException(404, "REVIEW_SOURCE_NOT_FOUND")
                    payload_id = link.payload_id
                    metadata = session.get(PayloadSample, payload_id).metadata_safe
                    if (
                        metadata.get("workflow") != body.workflow
                        or metadata.get("environment") != body.environment
                    ):
                        raise HTTPException(403, "REVIEW_SOURCE_SCOPE_DENIED")
                sources.append((source, payload_id))
            for pid in sorted(
                {session.get(PayloadSample, p).policy_id for _, p in sources}, key=str
            ):
                active_policy(session, project_id, pid)
            snapshots = []
            for source, payload_id in sources:
                value = _read(
                    session,
                    store,
                    project_id,
                    payload_id,
                    workflow=body.workflow,
                    environment=body.environment,
                )
                if source.kind == "dataset_item":
                    case = Case.model_validate({**value, "id": str(source.id)})
                else:
                    if not value.get("input"):
                        raise HTTPException(422, "REVIEW_SOURCE_INPUT_REQUIRED")
                    case = Case(
                        id=str(source.id), input=value["input"], reference=value.get("output")
                    )
                if body.mode == "calibration" and case.gold is None:
                    raise HTTPException(422, "CALIBRATION_REQUIRES_KNOWN_GOLD")
                snapshots.append(case)
            config = body.model_dump(mode="json")
            config["source_rights_basis"] = policy.rights_basis
            config["source_payload_ids"] = [str(p) for _, p in sources]
            config_payload = _write(
                session,
                store,
                project_id,
                body.policy_id,
                policy,
                config,
                refs,
                kind="review_configuration",
            )
            row = ReviewQueue(
                project_id=project_id,
                policy_id=body.policy_id,
                payload_id=config_payload.id,
                version_hash=digest(config),
                workflow=body.workflow,
                environment=body.environment,
                required_reviewers=body.required_reviewers,
                mode=body.mode,
            )
            session.add(row)
            session.flush()
            # Review makes these groups development evidence, including formerly held-out examples.
            families = register_groups(session, project_id, snapshots, actor=auth["actor"])
            try:
                require_unreserved_groups(session, project_id, set(families.values()))
            except LineageViolation as exc:
                raise HTTPException(409, str(exc)) from None
            for family in set(families.values()):
                session.add(
                    DatasetExposure(
                        project_id=project_id,
                        family_id=family,
                        dataset_id=None,
                        run_id=None,
                        source_ref="review:" + str(row.id),
                        mode="validation",
                        protected=False,
                    )
                )
            for (source, _), case in zip(sources, snapshots, strict=True):
                payload = _write(
                    session,
                    store,
                    project_id,
                    body.policy_id,
                    policy,
                    {"case": case.model_dump(mode="json")},
                    refs,
                    kind="review_source",
                )
                session.add(
                    ReviewItem(
                        project_id=project_id,
                        queue_id=row.id,
                        source_kind=source.kind,
                        source_id=source.id,
                        payload_id=payload.id,
                    )
                )
            _audit(
                session,
                project_id,
                auth["actor"],
                "review_queue_created",
                queue_id=str(row.id),
                items=len(sources),
                mode=body.mode,
            )
            return {
                **_summary(row),
                "content": "encrypted",
                "independence": "distinct_credentials_not_verified_humans",
            }

    @app.get("/v1/projects/{project_id}/reviews")
    def queues(
        project_id: UUID,
        before: UUID | None = None,
        limit: int = Query(25, ge=1, le=100),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            query = select(ReviewQueue).where(ReviewQueue.project_id == project_id)
            if before:
                query = query.where(ReviewQueue.id < before)
            rows = session.scalars(query.order_by(ReviewQueue.id.desc()).limit(limit + 1)).all()
            return {
                "items": [_summary(r) for r in rows[:limit]],
                "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
            }

    @app.get("/v1/projects/{project_id}/reviews/{queue_id}/items")
    def items(
        project_id: UUID,
        queue_id: UUID,
        before: UUID | None = None,
        limit: int = Query(50, ge=1, le=100),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            q = session.get(ReviewQueue, queue_id, with_for_update=True)
            if not q or q.project_id != project_id:
                raise HTTPException(404, "REVIEW_QUEUE_NOT_FOUND")
            query = select(ReviewItem).where(ReviewItem.queue_id == q.id)
            if before:
                query = query.where(ReviewItem.id > before)
            rows = session.scalars(query.order_by(ReviewItem.id).limit(limit + 1)).all()
            for r in rows:
                expire_leases(session, r)
            return {
                "items": [
                    {
                        "id": str(r.id),
                        "state": r.state,
                        "source_kind": r.source_kind,
                        "source_id": str(r.source_id),
                    }
                    for r in rows[:limit]
                ],
                "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
            }

    @app.post("/v1/projects/{project_id}/reviews/{queue_id}/claim")
    def claim(project_id: UUID, queue_id: UUID, body: ReviewClaim, auth=Depends(authorize)):
        scope(auth, project_id, ("analyst",))
        with transaction(project_id) as (session, store, _):
            q, config, _ = queue(session, store, project_id, queue_id)
            eligible(session, project_id, config, auth["actor"])
            if q.status != "open":
                raise HTTPException(409, "REVIEW_QUEUE_CLOSED")
            # Bounded queues and project lock make simultaneous claims deterministic.
            rows = session.scalars(
                select(ReviewItem).where(ReviewItem.queue_id == q.id).order_by(ReviewItem.id)
            ).all()
            states = [(r, *expire_leases(session, r)) for r in rows]
            for r, live, _ in states:
                for lease in live:
                    if lease.actor == auth["actor"]:
                        return {
                            "item_id": str(r.id),
                            "lease_id": str(lease.id),
                            "expires_at": lease.expires_at.isoformat(),
                            "queue_hash": q.version_hash,
                        }
            for r, live, votes in states:
                if (
                    r.state in {"adjudication", "resolved"}
                    or any(v.actor == auth["actor"] for v in votes)
                    or len(live) + len(votes) >= q.required_reviewers
                ):
                    continue
                lease = ReviewLease(
                    project_id=project_id,
                    item_id=r.id,
                    actor=auth["actor"],
                    expires_at=datetime.now(UTC) + timedelta(seconds=body.lease_seconds),
                )
                session.add(lease)
                session.flush()
                r.state = "leased"
                _audit(
                    session,
                    project_id,
                    auth["actor"],
                    "review_claimed",
                    queue_id=str(q.id),
                    item_id=str(r.id),
                    lease_id=str(lease.id),
                )
                return {
                    "item_id": str(r.id),
                    "lease_id": str(lease.id),
                    "expires_at": lease.expires_at.isoformat(),
                    "queue_hash": q.version_hash,
                }
            raise HTTPException(409, "NO_REVIEW_ITEM_AVAILABLE")

    @app.post("/v1/projects/{project_id}/reviews/{queue_id}/items/{item_id}/content")
    def content(
        project_id: UUID,
        queue_id: UUID,
        item_id: UUID,
        body: ContentPurpose,
        auth=Depends(authorize),
    ):
        scope(auth, project_id, ("analyst",))
        with transaction(project_id) as (session, store, _):
            q, config, _ = queue(session, store, project_id, queue_id)
            eligible(session, project_id, config, auth["actor"])
            r = item(session, project_id, q, item_id)
            live, votes = expire_leases(session, r)
            own = next((v for v in votes if v.actor == auth["actor"]), None)
            if not own and not any(lease.actor == auth["actor"] for lease in live):
                raise HTTPException(403, "ACTIVE_REVIEW_LEASE_OR_OWN_VOTE_REQUIRED")
            snapshot = read(session, store, q, r.payload_id)["case"]
            _audit(
                session,
                project_id,
                auth["actor"],
                "review_content_read",
                item_id=str(r.id),
                purpose=body.purpose,
            )
            return {
                "input": snapshot["input"],
                "reference": snapshot["reference"],
                "rubric": config["rubric"],
                "queue_hash": q.version_hash,
                "mode": q.mode,
                "own_vote": read(session, store, q, own.payload_id)["judgment"] if own else None,
                "peer_answers": "withheld",
                "source_gold": "withheld",
            }

    @app.post("/v1/projects/{project_id}/reviews/{queue_id}/items/{item_id}/release")
    def release(
        project_id: UUID,
        queue_id: UUID,
        item_id: UUID,
        body: ReviewRelease,
        auth=Depends(authorize),
    ):
        scope(auth, project_id, ("analyst",))
        with transaction(project_id) as (session, store, _):
            q, config, _ = queue(session, store, project_id, queue_id)
            eligible(session, project_id, config, auth["actor"])
            r = item(session, project_id, q, item_id)
            live, _ = expire_leases(session, r)
            for lease in live:
                if lease.actor == auth["actor"] and lease.id == body.lease_id:
                    lease.status = "released"
            expire_leases(session, r)
            _audit(
                session,
                project_id,
                auth["actor"],
                "review_lease_released",
                item_id=str(r.id),
                purpose=body.purpose,
            )
            return {"state": r.state}

    def resolve(session, store, refs, q, r, policy, judgment, actor, method):
        payload = _write(
            session,
            store,
            q.project_id,
            q.policy_id,
            policy,
            {"judgment": judgment},
            refs,
            kind="review_resolution",
        )
        session.add(
            ReviewResolution(
                project_id=q.project_id,
                item_id=r.id,
                actor=actor,
                method=method,
                payload_id=payload.id,
            )
        )
        r.state = "resolved"

    @app.post("/v1/projects/{project_id}/reviews/{queue_id}/items/{item_id}/votes", status_code=201)
    def vote(
        project_id: UUID,
        queue_id: UUID,
        item_id: UUID,
        body: ReviewSubmission,
        auth=Depends(authorize),
    ):
        scope(auth, project_id, ("analyst",))
        with transaction(project_id) as (session, store, refs):
            q, config, policy = queue(session, store, project_id, queue_id)
            eligible(session, project_id, config, auth["actor"])
            if body.queue_hash != q.version_hash or q.status != "open":
                raise HTTPException(409, "REVIEW_QUEUE_VERSION_CHANGED_OR_CLOSED")
            r = item(session, project_id, q, item_id)
            live, votes = expire_leases(session, r)
            judgment = body.model_dump(mode="json", exclude={"lease_id", "queue_hash"})
            own = next((v for v in votes if v.actor == auth["actor"]), None)
            if own:
                if (
                    own.lease_id == body.lease_id
                    and read(session, store, q, own.payload_id)["judgment"] == judgment
                ):
                    return {"id": str(own.id), "status": "duplicate", "state": r.state}
                raise HTTPException(409, "REVIEW_VOTES_ARE_IMMUTABLE")
            lease = next(
                (
                    lease
                    for lease in live
                    if lease.id == body.lease_id and lease.actor == auth["actor"]
                ),
                None,
            )
            if not lease or r.state in {"adjudication", "resolved"}:
                raise HTTPException(409, "REVIEW_LEASE_EXPIRED_OR_UNAVAILABLE")
            # Reading the source rechecks expiry/policy before accepting a label.
            read(session, store, q, r.payload_id)
            payload = _write(
                session,
                store,
                project_id,
                q.policy_id,
                policy,
                {"judgment": judgment},
                refs,
                kind="review_vote",
            )
            record = ReviewVote(
                project_id=project_id,
                item_id=r.id,
                actor=auth["actor"],
                lease_id=lease.id,
                payload_id=payload.id,
            )
            session.add(record)
            session.flush()
            lease.status = "submitted"
            votes.append(record)
            r.state = "reviewed"
            if len(votes) == q.required_reviewers:
                judgments = [read(session, store, q, v.payload_id)["judgment"] for v in votes]
                identities = {
                    digest({"decision": j["decision"], "gold": j["gold"]}) for j in judgments
                }
                if len(identities) == 1:
                    resolve(
                        session, store, refs, q, r, policy, judgments[0], "consensus", "consensus"
                    )
                else:
                    r.state = "adjudication"
            _audit(
                session,
                project_id,
                auth["actor"],
                "review_vote_submitted",
                item_id=str(r.id),
                vote_id=str(record.id),
                state=r.state,
            )
            return {"id": str(record.id), "state": r.state, "peer_answers": "withheld"}

    @app.post("/v1/projects/{project_id}/reviews/{queue_id}/items/{item_id}/adjudication-content")
    def adjudication_content(
        project_id: UUID,
        queue_id: UUID,
        item_id: UUID,
        body: ContentPurpose,
        auth=Depends(authorize),
    ):
        scope(auth, project_id, ("analyst",))
        with transaction(project_id) as (session, store, _):
            q, config, _ = queue(session, store, project_id, queue_id)
            eligible(session, project_id, config, auth["actor"], adjudicator=True)
            r = item(session, project_id, q, item_id)
            if r.state != "adjudication":
                raise HTTPException(409, "REVIEW_DISAGREEMENT_REQUIRED")
            votes = session.scalars(
                select(ReviewVote).where(ReviewVote.item_id == r.id).order_by(ReviewVote.id)
            ).all()
            snapshot = read(session, store, q, r.payload_id)["case"]
            _audit(
                session,
                project_id,
                auth["actor"],
                "review_adjudication_read",
                item_id=str(r.id),
                purpose=body.purpose,
            )
            return {
                "input": snapshot["input"],
                "reference": snapshot["reference"],
                "rubric": config["rubric"],
                "votes": [
                    {"id": str(v.id), "judgment": read(session, store, q, v.payload_id)["judgment"]}
                    for v in votes
                ],
                "queue_hash": q.version_hash,
            }

    @app.post(
        "/v1/projects/{project_id}/reviews/{queue_id}/items/{item_id}/adjudicate", status_code=201
    )
    def adjudicate(
        project_id: UUID,
        queue_id: UUID,
        item_id: UUID,
        body: ReviewAdjudication,
        auth=Depends(authorize),
    ):
        scope(auth, project_id, ("analyst",))
        with transaction(project_id) as (session, store, refs):
            q, config, policy = queue(session, store, project_id, queue_id)
            eligible(session, project_id, config, auth["actor"], adjudicator=True)
            r = item(session, project_id, q, item_id)
            if body.queue_hash != q.version_hash or r.state != "adjudication" or q.status != "open":
                raise HTTPException(409, "REVIEW_DISAGREEMENT_AND_FROZEN_VERSION_REQUIRED")
            read(session, store, q, r.payload_id)
            resolve(
                session,
                store,
                refs,
                q,
                r,
                policy,
                body.model_dump(mode="json", exclude={"queue_hash"}),
                auth["actor"],
                "adjudication",
            )
            _audit(session, project_id, auth["actor"], "review_adjudicated", item_id=str(r.id))
            return {"state": r.state}

    @app.post("/v1/projects/{project_id}/reviews/{queue_id}/promote", status_code=201)
    def promote(project_id: UUID, queue_id: UUID, body: ReviewPromotion, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with transaction(project_id) as (session, store, refs):
            q, config, _ = queue(session, store, project_id, queue_id)
            if q.mode != "labeling":
                raise HTTPException(409, "CALIBRATION_DATASET_CANNOT_BE_PROMOTED")
            if not body.rights_confirmed or body.queue_hash != q.version_hash:
                raise HTTPException(409, "REVIEW_FROZEN_VERSION_AND_RIGHTS_REQUIRED")
            if q.promoted_dataset_id:
                dataset = session.get(DatasetRecord, q.promoted_dataset_id)
                if dataset.provenance.get("review_promotion_hash") != digest(
                    body.model_dump(mode="json")
                ):
                    raise HTTPException(409, "REVIEW_PROMOTION_IMMUTABLE_CONFLICT")
                return {
                    "id": str(dataset.id),
                    "immutable_hash": dataset.immutable_hash,
                    "status": "duplicate",
                }
            if q.status != "open":
                raise HTTPException(409, "REVIEW_QUEUE_CLOSED")
            if any(
                not principal_active(session, project_id, actor)
                for actor in [*config["reviewers"], config["adjudicator"]]
            ):
                raise HTTPException(409, "REVIEW_PARTICIPANT_GRANT_CHANGED_CREATE_NEW_QUEUE")
            rows = session.scalars(
                select(ReviewItem).where(ReviewItem.queue_id == q.id).order_by(ReviewItem.id)
            ).all()
            if any(r.state != "resolved" for r in rows):
                raise HTTPException(409, "ALL_REVIEW_ITEMS_MUST_BE_RESOLVED")
            cases, receipts = [], []
            for r in rows:
                snapshot = read(session, store, q, r.payload_id)["case"]
                resolution = session.scalar(
                    select(ReviewResolution).where(ReviewResolution.item_id == r.id)
                )
                judgment = read(session, store, q, resolution.payload_id)["judgment"]
                votes = session.scalars(select(ReviewVote).where(ReviewVote.item_id == r.id)).all()
                for v in votes:
                    read(session, store, q, v.payload_id)
                receipts.append(
                    {
                        "item_id": str(r.id),
                        "resolution_id": str(resolution.id),
                        "method": resolution.method,
                        "vote_ids": sorted(str(v.id) for v in votes),
                        "source_kind": r.source_kind,
                        "source_id": str(r.source_id),
                    }
                )
                if judgment["decision"] == "label":
                    cases.append(
                        Case.model_validate({**snapshot, "id": str(r.id), "gold": judgment["gold"]})
                    )
            if not cases:
                raise HTTPException(409, "REVIEW_HAS_NO_INCLUDED_CASES")
            result = persist_dataset(
                session,
                store,
                project_id,
                DatasetImport(
                    workflow=q.workflow,
                    environment=q.environment,
                    policy_id=str(q.policy_id),
                    source_type="human_labeled",
                    independent_labels=body.independent_reviewers_attested,
                    representative=False,
                    provenance_ref=body.provenance_ref,
                    cases=cases,
                ),
                auth["actor"],
                refs,
                provenance_extra={
                    "review_queue_id": str(q.id),
                    "review_promotion_hash": digest(body.model_dump(mode="json")),
                    "review_attestation_actor": auth["actor"],
                    "review_queue_hash": q.version_hash,
                    "review_receipts": receipts,
                    "reviewer_independence": "owner_attested"
                    if body.independent_reviewers_attested
                    else "unverified",
                    "synthetic": config.get("source_rights_basis") == "synthetic",
                },
            )
            q.promoted_dataset_id = UUID(result["id"])
            q.status = "promoted"
            _audit(
                session,
                project_id,
                auth["actor"],
                "review_promoted",
                queue_id=str(q.id),
                dataset_id=result["id"],
            )
            return result

    @app.post("/v1/projects/{project_id}/reviews/{queue_id}/calibration")
    def calibration(
        project_id: UUID, queue_id: UUID, body: ContentPurpose, auth=Depends(authorize)
    ):
        scope(auth, project_id, ("admin",))
        with transaction(project_id) as (session, store, _):
            q, config, _ = queue(session, store, project_id, queue_id)
            if q.mode != "calibration":
                raise HTTPException(409, "CALIBRATION_QUEUE_REQUIRED")
            rows = session.scalars(select(ReviewItem).where(ReviewItem.queue_id == q.id)).all()
            if any(r.state != "resolved" for r in rows):
                raise HTTPException(409, "CALIBRATION_BLIND_REVIEW_NOT_FINISHED")
            scores = {actor: {"correct": 0, "total": 0} for actor in config["reviewers"]}
            for r in rows:
                gold = read(session, store, q, r.payload_id)["case"]["gold"]
                for vote in session.scalars(select(ReviewVote).where(ReviewVote.item_id == r.id)):
                    judgment = read(session, store, q, vote.payload_id)["judgment"]
                    scores[vote.actor]["total"] += 1
                    scores[vote.actor]["correct"] += int(
                        judgment["decision"] == "label" and digest(gold) == digest(judgment["gold"])
                    )
            _audit(
                session,
                project_id,
                auth["actor"],
                "review_calibration_read",
                queue_id=str(q.id),
                purpose=body.purpose,
            )
            return {
                "scores": scores,
                "agreement_metric": "exact_gold_match",
                "generalization": "unknown",
                "synthetic": True
                if config.get("source_rights_basis") == "synthetic"
                else "unknown",
            }
