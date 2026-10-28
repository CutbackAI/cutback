"""Bounded metadata-only consumer reports. They never authorize content or imply coverage."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Depends, HTTPException, Query
from sqlalchemy import func, select

from cutback.core.prompt_observation import PromptObservationBatch
from cutback.core.prompts import fingerprint
from cutback.server.dataset_lineage import lock_lineage
from cutback.server.prompt_db import (
    PromptAlias,
    PromptAliasChange,
    PromptDependency,
    PromptExperimentUse,
    PromptObservation,
    PromptRecord,
    PromptVersion,
)
from cutback.server.prompt_service import environment, prompt_record


def install_prompt_observations(app, sessions, authorize, scope):
    @app.post("/v1/ingest/prompt-observations", status_code=202)
    def ingest_prompt_observations(body: PromptObservationBatch, auth=Depends(authorize)):
        scope(auth, auth.get("project_id"), ("ingest",))
        project = auth["project_id"]
        accepted = duplicates = 0
        with sessions.begin() as session:
            lock_lineage(session, project)
            now = datetime.now(UTC)
            count = session.scalar(
                select(func.count())
                .select_from(PromptObservation)
                .where(PromptObservation.project_id == project)
            )
            for observation in body.observations:
                env = environment(session, project, observation.environment)
                version = session.get(PromptVersion, observation.version_id)
                record = session.get(PromptRecord, version.prompt_id) if version else None
                if (
                    env.id != auth["environment_id"]
                    or not version
                    or version.project_id != project
                    or not record
                    or record.workflow != observation.workflow
                    or version.document_hash != observation.version_hash
                ):
                    raise HTTPException(403, "PROMPT_OBSERVATION_SCOPE_OR_VERSION_DENIED")
                if (
                    not now - timedelta(days=30)
                    <= observation.occurred_at
                    <= now + timedelta(minutes=5)
                ):
                    raise HTTPException(422, "PROMPT_OBSERVATION_TIMESTAMP_OUTSIDE_WINDOW")
                if observation.alias_id:
                    alias = session.get(PromptAlias, observation.alias_id)
                    receipt = session.scalar(
                        select(PromptAliasChange).where(
                            PromptAliasChange.alias_id == observation.alias_id,
                            PromptAliasChange.revision == observation.alias_revision,
                        )
                    )
                    if (
                        not alias
                        or alias.project_id != project
                        or alias.environment_id != env.id
                        or not receipt
                        or receipt.to_version_id != version.id
                    ):
                        raise HTTPException(409, "PROMPT_OBSERVATION_ALIAS_RECEIPT_MISMATCH")
                raw = observation.model_dump(mode="json")
                hashed = fingerprint(raw)
                existing = session.scalar(
                    select(PromptObservation).where(
                        PromptObservation.project_id == project,
                        PromptObservation.observation_id == observation.observation_id,
                    )
                )
                if existing:
                    if existing.fingerprint != hashed:
                        raise HTTPException(409, "PROMPT_OBSERVATION_IDENTITY_CONFLICT")
                    duplicates += 1
                    continue
                if count + accepted >= 50000:
                    raise HTTPException(429, "PROJECT_PROMPT_OBSERVATION_QUOTA_EXCEEDED")
                session.add(
                    PromptObservation(
                        project_id=project,
                        environment_id=env.id,
                        version_id=version.id,
                        observation_id=observation.observation_id,
                        fingerprint=hashed,
                        metadata_record=raw,
                    )
                )
                session.flush()
                accepted += 1
        return {
            "accepted": accepted,
            "duplicates": duplicates,
            "usage_coverage": "unknown",
            "evidence": "externally_reported_metadata",
        }

    @app.get("/v1/projects/{project_id}/prompts/{prompt_id}/impact")
    def impact(
        project_id: UUID,
        prompt_id: UUID,
        days: int = Query(7, ge=1, le=30),
        after: UUID | None = None,
        limit: int = Query(50, ge=1, le=100),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            prompt_record(session, project_id, prompt_id)
            versions = select(PromptVersion.id).where(
                PromptVersion.prompt_id == prompt_id, PromptVersion.project_id == project_id
            )
            query = select(PromptObservation).where(
                PromptObservation.project_id == project_id,
                PromptObservation.version_id.in_(versions),
                PromptObservation.received_at >= datetime.now(UTC) - timedelta(days=days),
            )
            if after:
                query = query.where(PromptObservation.id > after)
            rows = session.scalars(query.order_by(PromptObservation.id).limit(limit + 1)).all()
            dependents = session.scalar(
                select(func.count())
                .select_from(PromptDependency)
                .where(
                    PromptDependency.project_id == project_id,
                    PromptDependency.dependency_id.in_(versions),
                )
            )
            experiments = session.scalar(
                select(func.count())
                .select_from(PromptExperimentUse)
                .where(
                    PromptExperimentUse.project_id == project_id,
                    PromptExperimentUse.version_id.in_(versions),
                )
            )
            from cutback.server.quality_db import QualityPromptUse

            quality_uses = session.scalar(
                select(func.count())
                .select_from(QualityPromptUse)
                .where(
                    QualityPromptUse.project_id == project_id,
                    QualityPromptUse.version_id.in_(versions),
                )
            )
            return {
                "items": [r.metadata_record for r in rows[:limit]],
                "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
                "days": days,
                "registered_dependency_edges": dependents,
                "registered_experiment_uses": experiments,
                "registered_online_quality_uses": quality_uses,
                "usage_coverage": "unknown",
                "evidence": "retained_externally_reported_metadata",
                "unobserved_consumers": "unknown",
                "source_records_may_expire": True,
            }
