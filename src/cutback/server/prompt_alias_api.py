"""Exact prompt alias CAS, immutable receipts and reviewed rollback. No model calls."""

import difflib
import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends, HTTPException, Query
from sqlalchemy import func, select

from cutback.core.prompts import fingerprint
from cutback.server.db import Environment, ExperimentRunRecord, RecommendationRecord
from cutback.server.evaluation_api import require_store
from cutback.server.principals import principal_role
from cutback.server.prompt_api import audit
from cutback.server.prompt_contracts import PromptDiff, PromptPromote, PromptRollback
from cutback.server.prompt_db import (
    PromptAlias,
    PromptAliasChange,
    PromptExperimentUse,
    PromptVersion,
)
from cutback.server.prompt_service import environment, load_graph, prompt_record


def alias_metadata(row, session):
    target = session.get(Environment, row.environment_id)
    return {
        "id": str(row.id),
        "prompt_id": str(row.prompt_id),
        "environment_id": str(row.environment_id),
        "environment": target.name if target else None,
        "name": row.name,
        "version_id": str(row.version_id),
        "revision": row.revision,
        "updated_at": row.updated_at.isoformat(),
    }


def receipt_metadata(row):
    return {
        "id": str(row.id),
        "alias_id": str(row.alias_id),
        "revision": row.revision,
        "from_version_id": str(row.from_version_id) if row.from_version_id else None,
        "to_version_id": str(row.to_version_id),
        "actor": row.actor,
        "approval_ref": row.approval_ref,
        "rollback_of": str(row.rollback_of) if row.rollback_of else None,
        "quality_status": row.quality_status,
        "evidence_run_id": str(row.evidence_run_id) if row.evidence_run_id else None,
        "evidence_model_key": row.evidence_model_key,
        "created_at": row.created_at.isoformat(),
    }


def require_alias_editor(session, project, auth):
    if principal_role(session, project, auth["actor"]) not in {"admin", "project_admin", "editor"}:
        raise HTTPException(403, "CURRENT_PROMPT_ALIAS_EDITOR_REQUIRED")


def evidence_status(app, session, project, body):
    if body.evidence_run_id is None:
        return "manual_quality_unverified"
    from cutback.server.dataset_lineage import require_current_test_evidence
    from cutback.server.db import ExperimentPlanRecord
    from cutback.server.plan_artifacts import load_plan

    run = session.get(ExperimentRunRecord, body.evidence_run_id)
    if (
        not run
        or run.project_id != project
        or run.status != "COMPLETED"
        or not run.results.get("billing_complete")
    ):
        raise HTTPException(409, "PROMPT_CONFIRMATION_RUN_REQUIRED")
    plan = session.get(ExperimentPlanRecord, run.plan_id)
    try:
        frozen = load_plan(require_store(app), project, plan.specification)
    except ValueError:
        raise HTTPException(409, "PROMPT_CONFIRMATION_ARTIFACT_UNAVAILABLE") from None
    bindings = getattr(frozen, "prompt_bindings", None) or []
    root_binding = next(
        (
            b
            for b in bindings
            if b.model_key == body.evidence_model_key and b.version_id == body.version_id
        ),
        None,
    )
    indexed = session.scalar(
        select(PromptExperimentUse.id).where(
            PromptExperimentUse.project_id == project,
            PromptExperimentUse.plan_id == run.plan_id,
            PromptExperimentUse.version_id == body.version_id,
            PromptExperimentUse.model_key == body.evidence_model_key,
        )
    )
    if not root_binding or not indexed or frozen.split != "test":
        raise HTTPException(409, "EXACT_PROMPT_TEST_BINDING_REQUIRED")
    version = session.get(PromptVersion, body.version_id)
    if root_binding.version_hash != version.document_hash:
        raise HTTPException(409, "PROMPT_EVIDENCE_VERSION_HASH_CHANGED")
    rows = session.scalars(
        select(RecommendationRecord).where(
            RecommendationRecord.project_id == project, RecommendationRecord.run_id == run.id
        )
    ).all()
    recommendation = next(
        (r for r in rows if r.evidence.get("candidate", {}).get("key") == body.evidence_model_key),
        None,
    )
    if not recommendation or not recommendation.evidence.get("quality_gate_passed"):
        raise HTTPException(409, "PROMPT_REQUIRED_QUALITY_GATES_NOT_PASSED")
    try:
        require_current_test_evidence(session, require_store(app), recommendation)
    except ValueError:
        raise HTTPException(409, "PROMPT_CONFIRMATION_LINEAGE_INVALID") from None
    strong = (
        recommendation.evidence.get("evidence_tier") == 4
        and recommendation.evidence.get("statistical_gate_passed") is True
        and recommendation.evidence.get("status") == "VERIFIED_ON_HELDOUT_DATASET"
    )
    if not strong and not body.quality_unverified_acknowledged:
        raise HTTPException(409, "PROMPT_QUALITY_STILL_UNVERIFIED_ACKNOWLEDGMENT_REQUIRED")
    return "confirmed_on_declared_dataset" if strong else "sample_checks_quality_unverified"


def install_prompt_aliases(app, sessions, authorize, scope, transaction):
    @app.get("/v1/projects/{project_id}/prompts/{prompt_id}/aliases")
    def aliases(
        project_id: UUID,
        prompt_id: UUID,
        after: UUID | None = None,
        limit: int = Query(50, ge=1, le=100),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            prompt_record(session, project_id, prompt_id)
            query = select(PromptAlias).where(
                PromptAlias.project_id == project_id, PromptAlias.prompt_id == prompt_id
            )
            if after:
                query = query.where(PromptAlias.id > after)
            rows = session.scalars(query.order_by(PromptAlias.id).limit(limit + 1)).all()
            return {
                "items": [alias_metadata(r, session) for r in rows[:limit]],
                "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
                "consumer_coverage": "unknown",
            }

    @app.get("/v1/projects/{project_id}/prompt-aliases/{alias_id}/history")
    def history(
        project_id: UUID,
        alias_id: UUID,
        before_revision: int | None = Query(None, ge=1),
        limit: int = Query(50, ge=1, le=100),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            alias = session.get(PromptAlias, alias_id)
            if not alias or alias.project_id != project_id:
                raise HTTPException(404, "PROMPT_ALIAS_NOT_FOUND")
            query = select(PromptAliasChange).where(PromptAliasChange.alias_id == alias_id)
            if before_revision:
                query = query.where(PromptAliasChange.revision < before_revision)
            rows = session.scalars(
                query.order_by(PromptAliasChange.revision.desc()).limit(limit + 1)
            ).all()
            return {
                "items": [receipt_metadata(r) for r in rows[:limit]],
                "next_cursor": rows[limit - 1].revision if len(rows) > limit else None,
            }

    @app.post("/v1/projects/{project_id}/prompts/{prompt_id}/aliases", status_code=201)
    def promote(project_id: UUID, prompt_id: UUID, body: PromptPromote, auth=Depends(authorize)):
        scope(auth, project_id, ("editor",))
        with transaction(project_id) as (session, store, _):
            require_alias_editor(session, project_id, auth)
            record = prompt_record(session, project_id, prompt_id)
            env = environment(session, project_id, body.environment)
            version = session.get(PromptVersion, body.version_id)
            if not version or version.project_id != project_id or version.prompt_id != prompt_id:
                raise HTTPException(404, "PINNED_PROMPT_VERSION_NOT_FOUND")
            loaded = load_graph(
                session,
                store,
                project_id,
                version.id,
                auth,
                env_name=env.name,
                workflow=record.workflow,
                purpose=body.approval_ref,
            )
            alias = session.scalar(
                select(PromptAlias)
                .where(
                    PromptAlias.prompt_id == prompt_id,
                    PromptAlias.environment_id == env.id,
                    PromptAlias.name == body.alias,
                )
                .with_for_update()
            )
            request_hash = fingerprint(body.model_dump(mode="json"))
            if alias:
                receipt = session.scalar(
                    select(PromptAliasChange).where(
                        PromptAliasChange.alias_id == alias.id,
                        PromptAliasChange.revision == alias.revision,
                    )
                )
                if (
                    alias.revision == body.expected_revision + 1
                    and receipt
                    and receipt.request_hash == request_hash
                    and alias.version_id == body.version_id
                ):
                    return {
                        "alias": alias_metadata(alias, session),
                        "receipt": receipt_metadata(receipt),
                        "status": "duplicate",
                    }
                if (
                    alias.revision != body.expected_revision
                    or alias.version_id != body.expected_version_id
                ):
                    raise HTTPException(409, "PROMPT_ALIAS_HEAD_CHANGED")
                before = alias.version_id
                alias.version_id, alias.revision, alias.updated_at = (
                    version.id,
                    alias.revision + 1,
                    datetime.now(UTC),
                )
            else:
                if body.expected_revision != 0:
                    raise HTTPException(409, "PROMPT_ALIAS_HEAD_CHANGED")
                if (
                    session.scalar(
                        select(func.count())
                        .select_from(PromptAlias)
                        .where(PromptAlias.project_id == project_id)
                    )
                    >= 10000
                ):
                    raise HTTPException(429, "PROMPT_ALIAS_COUNT_BOUND_EXCEEDED")
                before = None
                alias = PromptAlias(
                    project_id=project_id,
                    prompt_id=prompt_id,
                    environment_id=env.id,
                    name=body.alias,
                    version_id=version.id,
                    revision=1,
                )
                session.add(alias)
                session.flush()
            quality = evidence_status(app, session, project_id, body)
            receipt = PromptAliasChange(
                project_id=project_id,
                alias_id=alias.id,
                from_version_id=before,
                to_version_id=version.id,
                revision=alias.revision,
                actor=auth["actor"],
                approval_ref=body.approval_ref,
                request_hash=request_hash,
                quality_status=quality,
                evidence_run_id=body.evidence_run_id,
                evidence_model_key=body.evidence_model_key,
            )
            session.add(receipt)
            session.flush()
            audit(
                session,
                project_id,
                auth,
                "prompt_alias_promoted",
                alias_id=str(alias.id),
                revision=alias.revision,
                receipt_id=str(receipt.id),
                quality_status=quality,
                approval_ref=body.approval_ref,
            )
            return {
                "alias": alias_metadata(alias, session),
                "receipt": receipt_metadata(receipt),
                "expires_at": loaded.expires_at.isoformat(),
                "provider_calls": 0,
                "application_consumers_may_observe_new_alias": True,
            }

    @app.post("/v1/projects/{project_id}/prompt-aliases/{alias_id}/rollback", status_code=201)
    def rollback(project_id: UUID, alias_id: UUID, body: PromptRollback, auth=Depends(authorize)):
        scope(auth, project_id, ("editor",))
        with transaction(project_id) as (session, store, _):
            require_alias_editor(session, project_id, auth)
            alias = session.get(PromptAlias, alias_id, with_for_update=True)
            if not alias or alias.project_id != project_id:
                raise HTTPException(404, "PROMPT_ALIAS_NOT_FOUND")
            original = session.get(PromptAliasChange, body.receipt_id)
            if not original or original.project_id != project_id or original.alias_id != alias_id:
                raise HTTPException(404, "PROMPT_ALIAS_RECEIPT_NOT_FOUND")
            request_hash = fingerprint(body.model_dump(mode="json"))
            current = session.scalar(
                select(PromptAliasChange).where(
                    PromptAliasChange.alias_id == alias.id,
                    PromptAliasChange.revision == alias.revision,
                )
            )
            if (
                alias.revision == body.expected_revision + 1
                and current
                and current.rollback_of == original.id
                and current.request_hash == request_hash
            ):
                return {
                    "alias": alias_metadata(alias, session),
                    "receipt": receipt_metadata(current),
                    "status": "duplicate",
                }
            if not original.from_version_id:
                raise HTTPException(409, "INITIAL_PROMPT_ALIAS_HAS_NO_PRIOR_VERSION")
            if (
                alias.revision != body.expected_revision
                or original.revision != body.expected_revision
                or alias.version_id != original.to_version_id
            ):
                raise HTTPException(409, "PROMPT_ALIAS_HEAD_CHANGED")
            record = prompt_record(session, project_id, alias.prompt_id)
            from cutback.server.db import Environment

            env = session.get(Environment, alias.environment_id)
            load_graph(
                session,
                store,
                project_id,
                original.from_version_id,
                auth,
                env_name=env.name,
                workflow=record.workflow,
                purpose=body.approval_ref,
            )
            before = alias.version_id
            alias.version_id, alias.revision, alias.updated_at = (
                original.from_version_id,
                alias.revision + 1,
                datetime.now(UTC),
            )
            receipt = PromptAliasChange(
                project_id=project_id,
                alias_id=alias.id,
                from_version_id=before,
                to_version_id=alias.version_id,
                revision=alias.revision,
                actor=auth["actor"],
                approval_ref=body.approval_ref,
                request_hash=request_hash,
                quality_status="rollback_quality_unverified",
                rollback_of=original.id,
            )
            session.add(receipt)
            session.flush()
            audit(
                session,
                project_id,
                auth,
                "prompt_alias_rolled_back",
                alias_id=str(alias.id),
                revision=alias.revision,
                receipt_id=str(receipt.id),
                approval_ref=body.approval_ref,
            )
            return {
                "alias": alias_metadata(alias, session),
                "receipt": receipt_metadata(receipt),
                "provider_calls": 0,
                "application_consumers_may_observe_new_alias": True,
            }

    @app.post("/v1/projects/{project_id}/prompts/{prompt_id}/diff")
    def diff(project_id: UUID, prompt_id: UUID, body: PromptDiff, auth=Depends(authorize)):
        scope(auth, project_id, ("analyst",))
        with transaction(project_id) as (session, store, _):
            prompt_record(session, project_id, prompt_id)
            values, hashes = [], []
            for identifier in (body.left_version_id, body.right_version_id):
                version = session.get(PromptVersion, identifier)
                if (
                    not version
                    or version.project_id != project_id
                    or version.prompt_id != prompt_id
                ):
                    raise HTTPException(404, "PINNED_PROMPT_VERSION_NOT_FOUND")
                loaded = load_graph(
                    session,
                    store,
                    project_id,
                    identifier,
                    auth,
                    env_name=body.environment,
                    workflow=body.workflow,
                    purpose=body.purpose,
                )
                values.append(
                    json.dumps(
                        loaded.graph.documents[identifier].model_dump(mode="json"),
                        ensure_ascii=False,
                        indent=2,
                    ).splitlines(keepends=True)
                )
                hashes.append(version.document_hash)
            lines: list[str] = []
            size, truncated = 0, False
            for line in difflib.unified_diff(
                values[0],
                values[1],
                fromfile=str(body.left_version_id),
                tofile=str(body.right_version_id),
            ):
                size += len(line.encode())
                if len(lines) >= 2000 or size > 64000:
                    truncated = True
                    break
                lines.append(line)
            return {
                "diff": "".join(lines),
                "truncated": truncated,
                "left_hash": hashes[0],
                "right_hash": hashes[1],
                "scope": "frozen_document_fields_dependencies_remain_pinned",
                "provider_calls": 0,
            }
