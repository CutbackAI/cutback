"""Real retained metadata must be rechecked before it can bind a future program."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from integration.test_policy_activity_scope import event, intake
from sqlalchemy import delete, select
from support_policy_activity import frame

from cutback.core.policy_runtime_reference import freeze_reference
from cutback.server.db import Environment
from cutback.server.policy_activity_api import inspect_activity
from cutback.server.policy_activity_db import PolicyActivityRecord
from cutback.server.policy_runtime_reference import require_reference

pytestmark = pytest.mark.postgres


def prepared(api, tenant, *, active=True):
    policy, source, summary, now = frame(active=active)
    start = int((now - timedelta(seconds=2)).timestamp()) * 1_000_000_000
    body = event(
        workflow=source.workflow,
        service_name=summary.scope.service_name,
        source_commit=source.commit,
        policy_sha256=source.policy_sha256,
        call_site_id=summary.scope.call_site_id,
        started_ns=start,
        ended_ns=start + 1000,
        cache_operation_upper_usd="0.000001",
        cache_cost_basis_ref="owned-fixture",
    )
    if not active:
        body.update(
            cache_requested=False,
            state="inactive",
            native_sdk_invocations=1,
            cache_operations=0,
            cache_write="not_applicable",
            usage_origin="native_response",
        )
    assert intake(api, tenant, body).status_code == 200
    with api[2].state.sessions() as session:
        environment = session.scalar(
            select(Environment).where(
                Environment.project_id == UUID(tenant[0]), Environment.name == "staging"
            )
        )
        value = inspect_activity(session, UUID(tenant[0]), summary.scope)
    return policy, source, freeze_reference(source, value, now=now), environment, body


@pytest.mark.parametrize("active", [False, True])
def test_current_project_source_cost_and_raw_evidence_required_without_payload_reads(
    api, tenant, active
):
    policy, source, reference, environment, body = prepared(api, tenant, active=active)
    with api[2].state.sessions() as session:
        assert (
            require_reference(session, UUID(tenant[0]), environment, source, policy, reference)
            is reference
        )
        with pytest.raises(ValueError, match="PROJECT_ENVIRONMENT_OR_POLICY_CHANGED"):
            require_reference(session, uuid4(), environment, source, policy, reference)
        with pytest.raises(ValueError, match="CURRENT_SOURCE_AND_FRESH_WINDOW"):
            require_reference(
                session,
                UUID(tenant[0]),
                environment,
                source.model_copy(update={"commit": "f" * 40}),
                policy,
                reference,
            )
    # Late activity changes the frozen evidence even when source and policy are unchanged.
    assert intake(api, tenant, {**body, "event_id": str(uuid4())}).status_code == 200
    with api[2].state.sessions() as session:
        with pytest.raises(ValueError, match="RAW_ACTIVITY_CHANGED_OR_EXPIRED"):
            require_reference(session, UUID(tenant[0]), environment, source, policy, reference)


def test_deleted_raw_rows_and_different_declared_money_cannot_reconstruct_a_reference(api, tenant):
    policy, source, reference, environment, _ = prepared(api, tenant)
    with api[2].state.sessions.begin() as session:
        session.execute(
            delete(PolicyActivityRecord).where(PolicyActivityRecord.project_id == UUID(tenant[0]))
        )
    with api[2].state.sessions() as session:
        with pytest.raises(ValueError, match="RAW_ACTIVITY_CHANGED_OR_EXPIRED"):
            require_reference(session, UUID(tenant[0]), environment, source, policy, reference)
        with pytest.raises(ValueError, match="CURRENT_SOURCE_AND_FRESH_WINDOW"):
            require_reference(
                session,
                UUID(tenant[0]),
                environment,
                source,
                policy,
                reference,
                now=reference.expires_at,
            )


def test_frozen_observation_must_match_actual_declared_cache_rate(api, tenant):
    policy, source, reference, environment, body = prepared(api, tenant)
    # Check the ingest credential's rate claim against the encrypted source policy's declaration.
    with api[2].state.sessions.begin() as session:
        session.execute(
            delete(PolicyActivityRecord).where(PolicyActivityRecord.project_id == UUID(tenant[0]))
        )
    changed = {**body, "event_id": str(uuid4()), "cache_operation_upper_usd": "0.1"}
    assert intake(api, tenant, changed).status_code == 200
    with api[2].state.sessions() as session:
        value = inspect_activity(session, UUID(tenant[0]), reference.summary.scope)
        changed_reference = freeze_reference(source, value, now=datetime.now(UTC))
        with pytest.raises(ValueError, match="DECLARED_CACHE_COST_CHANGED"):
            require_reference(
                session, UUID(tenant[0]), environment, source, policy, changed_reference
            )
