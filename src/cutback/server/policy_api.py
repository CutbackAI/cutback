"""Explicit admin-only, purpose-audited encrypted policy imports."""

from uuid import UUID

from fastapi import Depends, HTTPException
from pydantic import UUID4, ConfigDict

from cutback.core.improvement_policy import SourceImprovementPolicy
from cutback.core.models import Identifier
from cutback.core.policy_execution import PolicyExecutionRef
from cutback.core.policy_producer import PolicySourceBinding
from cutback.server.campaign_content import content_transaction
from cutback.server.dataset_lineage import lock_lineage, lock_preparation_identity
from cutback.server.db import AuditEvent
from cutback.server.evaluation_api import ContentPurpose, require_store
from cutback.server.policy_service import put_policy


class SourcePolicyImport(ContentPurpose):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)
    request_id: UUID4
    dataset_id: UUID
    source: PolicySourceBinding
    key: Identifier
    policy: SourceImprovementPolicy


def install_source_policies(app, sessions, authorize, scope):
    authorized = Depends(authorize)

    @app.post(
        "/v1/projects/{project_id}/source-policies",
        status_code=201,
        response_model=PolicyExecutionRef,
    )
    def import_policy(project_id: UUID, body: SourcePolicyImport, auth=authorized):
        scope(auth, project_id, ("admin",))
        store = require_store(app)
        with content_transaction(sessions, store, project_id) as (session, refs):
            lock_lineage(session, project_id)
            lock_preparation_identity(session, "source-policy", body.request_id)
            try:
                reference = put_policy(
                    session,
                    store,
                    project_id,
                    body.dataset_id,
                    body.source,
                    body.key,
                    body.policy,
                    body.request_id,
                    refs,
                )
            except ValueError:
                raise HTTPException(409, "CURRENT_SCOPED_POLICY_IMPORT_REQUIRED") from None
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="source_policy_import",
                    details={"payload_id": str(reference.payload_id), "purpose": body.purpose},
                )
            )
        return reference
