"""Current-policy prompt graph reads. A dependency UUID is never a content permission."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from fastapi import HTTPException
from sqlalchemy import select

from cutback.core.prompts import PromptDocument, PromptError, PromptGraph, fingerprint
from cutback.evals.datasets import digest
from cutback.evals.metrics import MetricSpec
from cutback.server.content_policy import policy_content
from cutback.server.db import AuditEvent, Environment, PayloadSample
from cutback.server.evaluation_api import active_policy, policy_object_count
from cutback.server.principals import principal_role
from cutback.server.prompt_db import PromptDependency, PromptReadGrant, PromptRecord, PromptVersion

READ_ROLES = {"admin", "project_admin", "analyst", "editor", "prompt_reader"}


def environment(session, project, name):
    row = session.scalar(
        select(Environment).where(Environment.project_id == project, Environment.name == name)
    )
    if not row:
        raise HTTPException(422, "PROMPT_ENVIRONMENT_NOT_CONFIGURED")
    return row


def check_read_access(session, project, auth, version, payload, target_env, workflow):
    role = principal_role(session, project, auth["actor"])
    if auth.get("project_id") is not None and auth["project_id"] != project:
        raise HTTPException(403, "PROMPT_PROJECT_SCOPE_DENIED")
    if role not in READ_ROLES:
        raise HTTPException(403, "CURRENT_PROMPT_PRINCIPAL_REQUIRED")
    if (
        role == "prompt_reader"
        and auth["role"] != "oidc"
        and auth["environment_id"] != target_env.id
    ):
        raise HTTPException(403, "PROMPT_READER_ENVIRONMENT_SCOPE_DENIED")
    if (
        role in {"admin", "project_admin"}
        or version.creator == auth["actor"]
        and role in {"analyst", "editor"}
    ):
        return payload.expires_at
    grant = session.scalar(
        select(PromptReadGrant).where(
            PromptReadGrant.project_id == project,
            PromptReadGrant.actor == auth["actor"],
            PromptReadGrant.prompt_id == version.prompt_id,
            PromptReadGrant.environment_id == target_env.id,
            PromptReadGrant.workflow == workflow,
            PromptReadGrant.policy_id == payload.policy_id,
            PromptReadGrant.enabled.is_(True),
            PromptReadGrant.expires_at > datetime.now(UTC),
        )
    )
    if not grant:
        raise HTTPException(403, "EXPLICIT_PROMPT_CONTENT_GRANT_REQUIRED")
    return min(payload.expires_at, grant.expires_at)


def prompt_record(session, project, identifier):
    row = session.get(PromptRecord, identifier)
    if not row or row.project_id != project:
        raise HTTPException(404, "PROMPT_NOT_FOUND")
    return row


def validate_schema(document):
    if document.output_schema is not None:
        try:
            MetricSpec(kind="json_schema", minimum=1, schema_definition=document.output_schema)
        except (ValueError, TypeError):
            raise HTTPException(422, "PROMPT_OUTPUT_SCHEMA_PROFILE_INVALID") from None


@dataclass
class LoadedPrompt:
    graph: PromptGraph
    versions: dict[UUID, PromptVersion]
    policies: dict
    expires_at: datetime
    content_receipts: dict

    def response(self):
        root = self.versions[self.graph.root]
        return {
            "root_version_id": str(root.id),
            "version_hash": root.document_hash,
            "version_hashes": {
                str(key): version.document_hash for key, version in self.versions.items()
            },
            "documents": {
                str(key): value.model_dump(mode="json")
                for key, value in self.graph.documents.items()
            },
            "content_receipts": {str(key): value for key, value in self.content_receipts.items()},
            "expires_at": self.expires_at.isoformat(),
            "provider_calls": 0,
        }


def load_graph(
    session,
    store,
    project,
    identifier,
    auth,
    *,
    env_name,
    workflow,
    provider=None,
    purpose=None,
    _proposed_document=None,
):
    target = environment(session, project, env_name)
    documents: dict[UUID, PromptDocument] = {}
    versions: dict[UUID, PromptVersion] = {}
    policies, deadlines, receipts = {}, [], {}
    active: set[UUID] = set()

    def visit(version_id, depth):
        if version_id in active:
            raise PromptError("PROMPT_DEPENDENCY_CYCLE")
        if depth > 8:
            raise PromptError("PROMPT_DEPENDENCY_DEPTH_EXCEEDED")
        if version_id in documents:
            return
        if len(documents) + len(active) >= 32:
            raise PromptError("PROMPT_DEPENDENCY_COUNT_EXCEEDED")
        if version_id == identifier and _proposed_document is not None:
            active.add(version_id)
            for child in _proposed_document.dependencies.values():
                visit(child, depth + 1)
            active.remove(version_id)
            documents[version_id] = _proposed_document
            return
        version = session.get(PromptVersion, version_id)
        if not version or version.project_id != project:
            raise HTTPException(404, "PINNED_PROMPT_VERSION_NOT_FOUND")
        record = prompt_record(session, project, version.prompt_id)
        source_env = session.get(Environment, version.environment_id)
        payload = session.get(PayloadSample, version.payload_id)
        if (
            not payload
            or payload.project_id != project
            or payload.expires_at <= datetime.now(UTC)
            or payload.metadata_safe.get("deleted")
        ):
            raise HTTPException(410, "PROMPT_CONTENT_EXPIRED_OR_UNAVAILABLE")
        if not source_env or source_env.project_id != project:
            raise HTTPException(410, "PROMPT_SOURCE_ENVIRONMENT_UNAVAILABLE")
        _, policy = active_policy(session, project, payload.policy_id)
        if not policy.permits(source_env.name, record.workflow) or not policy.permits(
            env_name, workflow
        ):
            raise HTTPException(403, "PROMPT_SOURCE_OR_CONSUMER_POLICY_SCOPE_DENIED")
        if provider is not None and provider not in policy.allowed_providers:
            raise HTTPException(403, "PROMPT_PROVIDER_POLICY_SCOPE_DENIED")
        deadline = check_read_access(session, project, auth, version, payload, target, workflow)
        try:
            value = json.loads(store.get(project, payload.object_ref))
            if (
                digest(value) != payload.content_hash
                or version.document_hash != payload.content_hash
            ):
                raise ValueError("PROMPT_CONTENT_INTEGRITY_FAILURE")
            policy_content(value, policy, preserve_semantics=True)
            document = PromptDocument.model_validate(value["document"])
            if fingerprint(document.model_dump(mode="json")) != fingerprint(value["document"]):
                raise ValueError("PROMPT_DOCUMENT_INTEGRITY_FAILURE")
        except (ValueError, KeyError, OSError, InvalidTag):
            raise HTTPException(410, "PROMPT_CONTENT_EXPIRED_OR_UNAVAILABLE") from None
        validate_schema(document)
        # The relational dependency index must describe exactly the frozen encrypted body.
        indexed = {
            r.alias: r.dependency_id
            for r in session.scalars(
                select(PromptDependency).where(
                    PromptDependency.project_id == project,
                    PromptDependency.version_id == version_id,
                )
            )
        }
        if indexed != document.dependencies:
            raise HTTPException(409, "PROMPT_DEPENDENCY_INDEX_INTEGRITY_FAILURE")
        versions[version_id], policies[version_id] = version, policy
        receipts[version_id] = {
            "receipt_nonce": value["receipt_nonce"],
            "request_fingerprint": value["request_fingerprint"],
        }
        deadlines.extend([deadline, policy.expires_at])
        active.add(version_id)
        for child in document.dependencies.values():
            visit(child, depth + 1)
        active.remove(version_id)
        documents[version_id] = document

    visit(identifier, 0)
    graph = PromptGraph(identifier, documents)
    graph.inspect()  # Includes longest-path validation for reused DAG components.
    if purpose is not None:
        session.add(
            AuditEvent(
                project_id=project,
                actor=auth["actor"],
                action="prompt_graph_content_read",
                details={
                    "root_version_id": str(identifier),
                    "versions": len(versions),
                    "environment_id": str(target.id),
                    "workflow": workflow,
                    "provider": provider,
                    "purpose": purpose,
                },
            )
        )
    return LoadedPrompt(
        graph,
        versions,
        policies,
        min(deadlines, default=datetime.max.replace(tzinfo=UTC)),
        receipts,
    )


def validate_composition(
    session, store, project, identifier, document, auth, *, env_name, workflow
):
    """Validate a proposed graph while the root version does not exist yet."""
    load_graph(
        session,
        store,
        project,
        identifier,
        auth,
        env_name=env_name,
        workflow=workflow,
        _proposed_document=document,
    )
    validate_schema(document)


def write_version_payload(
    session, store, project, policy_id, policy, document, refs, *, request_fingerprint
):
    if policy_object_count(session, policy_id) >= policy.max_samples:
        raise HTTPException(429, "CAPTURE_SAMPLE_QUOTA_EXCEEDED")
    value = {
        "document": document.model_dump(mode="json"),
        "receipt_nonce": uuid4().hex,
        "request_fingerprint": request_fingerprint,
    }
    try:
        clean, blob = policy_content(value, policy, preserve_semantics=True)
    except ValueError:
        raise HTTPException(422, "PROMPT_CONTENT_REQUIRES_APPROVED_DEIDENTIFIED_BYTES") from None
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
        metadata_safe={"kind": "prompt_version"},
    )
    session.add(row)
    session.flush()
    return row
