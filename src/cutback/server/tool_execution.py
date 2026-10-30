"""Remote scoped tool environment. This worker module never imports Docker control."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cutback.core.tool_policy import IsolatedToolPolicy
from cutback.evals.datasets import digest
from cutback.evals.isolated_tools import IsolatedEpisodeTools
from cutback.server.tool_ledger import ToolJournal
from cutback.tool_client import ToolBrokerClient


def configured_tool_client(spec, *, containment=False):
    if os.getenv("CUTBACK_TOOL_EXECUTION", "false").lower() != "true" and not (
        containment and os.getenv("CUTBACK_TOOL_CONTAINMENT", "false").lower() == "true"
    ):
        raise ValueError("ISOLATED_TOOL_EXECUTION_DISABLED")
    if (
        os.getenv("CUTBACK_TOOL_BROKER_PROJECT") != str(spec.project_id)
        or os.getenv("CUTBACK_TOOL_BROKER_PRINCIPAL") != str(spec.principal_id)
        or os.getenv("CUTBACK_TOOL_BROKER_ID") != str(spec.broker_id)
        or not containment
        and os.getenv("CUTBACK_TOOL_BROKER_IMAGE_ID") != spec.image_id
    ):
        raise ValueError("ISOLATED_TOOL_WORKER_SCOPE_NOT_CONFIGURED")
    return ToolBrokerClient(
        os.environ["CUTBACK_TOOL_BROKER_ORIGIN"],
        os.environ["CUTBACK_TOOL_BROKER_TOKEN"],
        project_id=spec.project_id,
        principal_id=spec.principal_id,
        image_id=spec.image_id,
        broker_id=spec.broker_id,
        allow_insecure_loopback=os.getenv("CUTBACK_TOOL_BROKER_ALLOW_LOOPBACK_HTTP", "false")
        == "true",
    )


def environment_factory(ledger, client, check_authority, *, content_expires_at):
    plan = ledger.plan
    spec = plan.episode_execution.isolated_tools
    if (client.project_id, client.principal_id, client.image_id, client.broker_id) != (
        spec.project_id,
        spec.principal_id,
        spec.image_id,
        spec.broker_id,
    ):
        raise ValueError("ISOLATED_TOOL_CONFIGURED_CLIENT_SCOPE_MISMATCH")

    def create(scenario):
        check_authority()
        now = datetime.now(UTC)
        expiry = min(content_expires_at, now + timedelta(seconds=spec.max_wall_seconds))
        remaining = int((expiry - now).total_seconds())
        if remaining < 1:
            raise ValueError("ISOLATED_TOOL_CONTENT_OR_RUN_WALL_EXPIRED")
        policy = IsolatedToolPolicy(
            project_id=spec.project_id,
            principal_id=spec.principal_id,
            environment=spec.environment,
            workflow=plan.workflow,
            session_id=uuid4(),
            image_id=spec.image_id,
            grants=tuple(scenario.grants),
            write_approved=scenario.write_approved,
            max_calls=scenario.max_tool_calls,
            max_wall_seconds=remaining,
            max_result_bytes=spec.max_result_bytes,
            expires_at=expiry,
            content_use_approved=True,
        )
        return IsolatedEpisodeTools(
            scenario,
            policy,
            client,
            ToolJournal(ledger),
            approval_hash=digest(plan.model_dump(mode="json")),
            check_authority=check_authority,
        )

    return create
