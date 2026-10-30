"""Opt-in encrypted experiment records with shared policy quotas and audited reads."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from cryptography.exceptions import InvalidTag
from fastapi import Depends, HTTPException, Query
from pydantic import model_validator
from sqlalchemy import select, true

from cutback.core.episodes import EpisodeScenario
from cutback.core.isolated_episodes import IsolatedRecordScenario
from cutback.core.models import Identifier, StrictModel
from cutback.evals.datasets import digest
from cutback.server.content_policy import policy_content
from cutback.server.db import (
    AuditEvent,
    DatasetRecord,
    ExperimentPlanRecord,
    ExperimentRunRecord,
    PayloadSample,
)


def recorder(sessions, store, project_id, run_id, policy_id):
    def record(value):
        from cutback.server.evaluation_api import active_policy, policy_object_count

        ref = None
        try:
            with sessions.begin() as session:
                _, policy = active_policy(session, project_id, UUID(policy_id))
                used = policy_object_count(session, UUID(policy_id))
                if used >= policy.max_samples:
                    raise ValueError("CASE_EVIDENCE_POLICY_QUOTA_EXCEEDED")
                clean, blob = policy_content(value, policy)
                episode_unknown = value.get("episode_assessment", {}).get("status") in {
                    "unknown",
                    "error",
                }
                ref = store.put(project_id, blob, policy.retention_days)
                session.add(
                    PayloadSample(
                        project_id=project_id,
                        policy_id=UUID(policy_id),
                        object_ref=ref,
                        content_hash=digest(clean),
                        expires_at=min(
                            policy.expires_at,
                            datetime.now(UTC) + timedelta(days=policy.retention_days),
                        ),
                        metadata_safe={
                            "kind": "experiment_case",
                            "run_id": str(run_id),
                            "model_key": clean.get("model_key"),
                            "case_hash": digest(value["case"]["id"]),
                            "stratum_hash": digest(value["case"].get("stratum", "default")),
                            "sample_unknown": episode_unknown,
                            "sample_failed": not episode_unknown
                            and (
                                not value.get("complete", True)
                                or (
                                    value.get("metric_value") is not None
                                    and value["metric_value"] < value.get("metric_minimum", 0)
                                )
                                or any(
                                    item["value"] is not None and item["value"] < item["minimum"]
                                    for item in value.get("criteria", [])
                                    if item.get("required", True)
                                )
                            ),
                            "failed_criteria": [
                                item["key"]
                                for item in value.get("criteria", [])
                                if item["value"] is not None and item["value"] < item["minimum"]
                            ],
                        },
                    )
                )
        except BaseException:
            if ref:
                try:
                    with sessions() as session:
                        referenced = session.scalar(
                            select(PayloadSample.id).where(
                                PayloadSample.project_id == project_id,
                                PayloadSample.object_ref == ref,
                            )
                        )
                    if not referenced:
                        store.delete(project_id, ref)
                except Exception:
                    # An uncertain COMMIT is not proof that the encrypted object is orphaned.
                    pass
            raise

    return record


class CorrectionInput(StrictModel):
    purpose: Identifier
    case_id: Identifier
    gold: str | list | dict | None = None
    episode: EpisodeScenario | IsolatedRecordScenario | None = None
    correction_ref: Identifier
    all_labels_reviewed: bool = False

    @model_validator(mode="after")
    def exactly_one_correction(self):
        if (self.gold is None) == (self.episode is None):
            raise ValueError("ONE_LABEL_OR_EPISODE_CORRECTION_REQUIRED")
        if self.episode is not None and self.all_labels_reviewed:
            raise ValueError("EPISODE_REVISION_DOES_NOT_CREATE_INDEPENDENT_GOLD")
        return self


def install_case_evidence(app, sessions, authorize, scope, *, import_dataset):
    from cutback.server.evaluation_api import ContentPurpose, active_policy, require_store

    def authorized_run(session, project_id, run_id):
        run = session.get(ExperimentRunRecord, run_id)
        if not run or run.project_id != project_id:
            raise HTTPException(404, "RUN_NOT_FOUND")
        plan = session.get(ExperimentPlanRecord, run.plan_id)
        active_policy(session, project_id, UUID(plan.specification["policy_id"]))
        return plan

    @app.post("/v1/projects/{project_id}/experiments/{run_id}/cases/paired")
    def paired(
        project_id: UUID,
        run_id: UUID,
        body: ContentPurpose,
        limit: int = Query(5, ge=1, le=10),
        offset: int = Query(0, ge=0, le=10000),
        failed_only: bool = True,
        model_key: str | None = Query(None, max_length=200),
        stratum: str | None = Query(None, max_length=200),
        criterion: str | None = Query(None, max_length=200),
        auth=Depends(authorize),
    ):
        from sqlalchemy import cast, func
        from sqlalchemy.dialects.postgresql import JSONB

        scope(auth, project_id, ("admin",))
        store = require_store(app)
        with sessions.begin() as session:
            plan = authorized_run(session, project_id, run_id)
            _, policy = active_policy(session, project_id, UUID(plan.specification["policy_id"]))
            filters = [
                PayloadSample.project_id == project_id,
                PayloadSample.metadata_safe["kind"].as_string() == "experiment_case",
                PayloadSample.metadata_safe["run_id"].as_string() == str(run_id),
                PayloadSample.expires_at > datetime.now(UTC),
                PayloadSample.metadata_safe["deleted"].as_boolean().is_not(True),
            ]
            anchors = list(filters)
            anchors.append(PayloadSample.metadata_safe["case_hash"].as_string().is_not(None))
            if failed_only:
                anchors.append(PayloadSample.metadata_safe["sample_failed"].as_boolean().is_(True))
            if model_key:
                anchors.append(PayloadSample.metadata_safe["model_key"].as_string() == model_key)
            if stratum:
                anchors.append(
                    PayloadSample.metadata_safe["stratum_hash"].as_string() == digest(stratum)
                )
            if criterion:
                anchors.append(
                    cast(PayloadSample.metadata_safe["failed_criteria"], JSONB).contains(
                        [criterion]
                    )
                )
            case_hash = PayloadSample.metadata_safe["case_hash"].as_string().label("case_hash")
            hashes = session.scalars(
                select(case_hash)
                .where(*anchors)
                .distinct()
                .order_by("case_hash")
                .offset(offset)
                .limit(limit + 1)
            ).all()
            chosen = hashes[:limit]
            rows = session.scalars(
                select(PayloadSample)
                .where(*filters, PayloadSample.metadata_safe["case_hash"].as_string().in_(chosen))
                .order_by(
                    PayloadSample.metadata_safe["case_hash"].as_string(),
                    PayloadSample.metadata_safe["model_key"].as_string(),
                    PayloadSample.id,
                )
                .limit(301)
            ).all()
            if len(rows) > 300:
                raise HTTPException(413, "CASE_COMPARISON_TOO_LARGE")
            values: dict[str, dict] = {}
            used = 0
            for row in rows:
                try:
                    blob = store.get(project_id, row.object_ref)
                    used += len(blob)
                    if used > 8 * 1048576:
                        raise HTTPException(413, "CASE_COMPARISON_TOO_LARGE")
                    value = json.loads(blob)
                    if digest(value) != row.content_hash:
                        raise ValueError("CASE_EVIDENCE_INTEGRITY_FAILED")
                    value, _ = policy_content(value, policy)
                except (OSError, ValueError, InvalidTag):
                    raise HTTPException(410, "CASE_EVIDENCE_EXPIRED_OR_UNAVAILABLE") from None
                key = row.metadata_safe["case_hash"]
                item = values.setdefault(key, {"case": value.get("case", {}), "outputs": []})
                item["outputs"].append({key: val for key, val in value.items() if key != "case"})
            unindexed = session.scalar(
                select(func.count())
                .select_from(PayloadSample)
                .where(
                    *filters, PayloadSample.metadata_safe["sample_failed"].as_boolean().is_(None)
                )
            )
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="experiment_paired_case_content_read",
                    details={"run_id": str(run_id), "purpose": body.purpose, "count": len(values)},
                )
            )
        return {
            "cases": list(values.values()),
            "offset": offset,
            "limit": limit,
            "more_possible": len(hashes) > limit,
            "unindexed_legacy_outputs": unindexed,
        }

    @app.post("/v1/projects/{project_id}/experiments/{run_id}/cases/correct", status_code=201)
    def correct(project_id: UUID, run_id: UUID, body: CorrectionInput, auth=Depends(authorize)):
        from cutback.evals.datasets import DatasetImport
        from cutback.server.worker import load_dataset

        scope(auth, project_id, ("admin",))
        store = require_store(app)
        with sessions.begin() as session:
            plan = authorized_run(session, project_id, run_id)
            dataset = session.get(DatasetRecord, plan.dataset_id)
            loaded, _ = load_dataset(session, dataset, store)
            cases = [item["case"] for item in loaded["items"]]
            matched = [case for case in cases if case["id"] == body.case_id]
            if len(matched) != 1:
                raise HTTPException(404, "CASE_NOT_FOUND_IN_DATASET")
            if body.episode is not None:
                if (
                    not matched[0].get("episode")
                    or dataset.provenance["source_type"] != "synthetic_fixture"
                ):
                    raise HTTPException(409, "SYNTHETIC_EPISODE_SOURCE_REQUIRED")
                matched[0]["episode"] = body.episode.model_dump(mode="json")
            else:
                if matched[0].get("episode"):
                    raise HTTPException(409, "EPISODE_ASSERTION_REVISION_REQUIRED")
                matched[0]["gold"] = body.gold
            fields = set(DatasetImport.model_fields) - {"cases"}
            provenance = {key: val for key, val in dataset.provenance.items() if key in fields}
            provenance.update(
                provenance_ref=body.correction_ref,
                representative=False,
                independent_labels=body.all_labels_reviewed,
            )
            original_id, original_hash = str(dataset.id), dataset.immutable_hash
            prepared = DatasetImport.model_validate({**provenance, "cases": cases})
        # A new import consumes the same shared policy quota; frozen evidence is never edited.
        created = import_dataset(project_id, prepared, auth)
        with sessions.begin() as session:
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="dataset_correction_version_created",
                    details={
                        "parent_dataset_id": original_id,
                        "parent_hash": original_hash,
                        "dataset_id": created["id"],
                        "case_hash": digest(body.case_id),
                        "purpose": body.purpose,
                        "correction_ref": body.correction_ref,
                    },
                )
            )
        return {
            **created,
            "parent_dataset_id": original_id,
            "original_evidence_changed": False,
            "new_experiment_approval_required": True,
        }

    @app.post("/v1/projects/{project_id}/experiments/{run_id}/cases/content")
    def content(
        project_id: UUID,
        run_id: UUID,
        body: ContentPurpose,
        limit: int = Query(25, ge=1, le=50),
        offset: int = Query(0, ge=0, le=10000),
        legacy_only: bool = False,
        auth=Depends(authorize),
    ):
        scope(auth, project_id, ("admin",))
        store = require_store(app)
        with sessions.begin() as session:
            run = session.get(ExperimentRunRecord, run_id)
            if not run or run.project_id != project_id:
                raise HTTPException(404, "RUN_NOT_FOUND")
            plan = session.get(ExperimentPlanRecord, run.plan_id)
            _, policy = active_policy(session, project_id, UUID(plan.specification["policy_id"]))
            rows = session.scalars(
                select(PayloadSample)
                .where(
                    PayloadSample.project_id == project_id,
                    PayloadSample.metadata_safe["kind"].as_string() == "experiment_case",
                    PayloadSample.metadata_safe["run_id"].as_string() == str(run_id),
                    PayloadSample.expires_at > datetime.now(UTC),
                    PayloadSample.metadata_safe["deleted"].as_boolean().is_not(True),
                    PayloadSample.metadata_safe["sample_failed"].as_boolean().is_(None)
                    if legacy_only
                    else true(),
                )
                .order_by(
                    PayloadSample.metadata_safe["case_hash"].as_string(),
                    PayloadSample.metadata_safe["model_key"].as_string(),
                    PayloadSample.id,
                )
                .offset(offset)
                .limit(limit)
            ).all()
            values = []
            for row in rows:
                try:
                    value = json.loads(store.get(project_id, row.object_ref))
                except (FileNotFoundError, ValueError, InvalidTag):
                    raise HTTPException(410, "CASE_EVIDENCE_EXPIRED_OR_UNAVAILABLE") from None
                if digest(value) != row.content_hash:
                    raise HTTPException(410, "CASE_EVIDENCE_INTEGRITY_FAILED")
                value, _ = policy_content(value, policy)
                values.append(value)
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="experiment_case_content_read",
                    details={"run_id": str(run_id), "purpose": body.purpose, "count": len(values)},
                )
            )
        return {
            "cases": values,
            "offset": offset,
            "limit": limit,
            "more_possible": len(values) == limit,
            "content_scope": "explicitly_authorized_encrypted_outputs",
        }
