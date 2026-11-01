"""Parse-only current-source binding and read-only inspection of activity metadata."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import TypeAdapter

from cutback.core.models import Identifier
from cutback.core.policy_activity import (
    PolicyRuntimeScope,
    PolicyRuntimeServiceQuery,
    PolicyRuntimeServices,
    PolicyRuntimeSummary,
)


def inspect_current(
    root,
    control,
    project,
    *,
    producer_file,
    service_name=None,
    environment="staging",
    window_minutes=60,
):
    from cutback.application_checks import snapshot
    from cutback.policy_source import inspect_configuration
    from cutback.scanner import scan

    if type(window_minutes) is not int or not 1 <= window_minutes <= 43200:
        raise ValueError("POLICY_ACTIVITY_BOUNDED_INSPECTION_WINDOW_REQUIRED")
    project = str(UUID(str(project)))
    if UUID(project).int == 0:
        raise ValueError("POLICY_ACTIVITY_NONZERO_PROJECT_REQUIRED")
    if service_name is not None:
        service_name = TypeAdapter(Identifier).validate_python(service_name)
    environment = TypeAdapter(Identifier).validate_python(environment)
    before = snapshot(root)
    policy, source = inspect_configuration(root, producer_file)
    inventory = scan(root)
    sites = [
        site
        for site in inventory["call_sites"]
        if site["source_file"] == source.producer_file
        and "cutback_policy_native_wrapper" in site["evidence"]
    ]
    if len(sites) != 1 or source.commit != before.commit or snapshot(root) != before:
        raise ValueError("POLICY_ACTIVITY_CURRENT_EXACT_SOURCE_CALL_SITE_REQUIRED")
    now = datetime.now(UTC)
    query = PolicyRuntimeServiceQuery(
        environment=environment,
        workflow=policy.workflow,
        call_site_id=sites[0]["call_site_id"],
        source_commit=source.commit,
        policy_sha256=policy.policy_sha256,
        window_start=now - timedelta(minutes=window_minutes),
        window_end=now,
    )
    ready = control("GET", "/v1/health/ready")
    if (
        not isinstance(ready, dict)
        or ready.get("source_policy_runtime_contract") != "cutback.source-policy-runtime.v1"
    ):
        raise ValueError("POLICY_ACTIVITY_MATCHING_COLLECTOR_REQUIRED")
    discovered_environment = None
    if service_name is None:
        if (
            ready.get("source_policy_runtime_services_contract")
            != "cutback.source-policy-runtime-services.v1"
        ):
            raise ValueError("POLICY_ACTIVITY_SERVICE_DISCOVERY_UPGRADE_REQUIRED")
        services = PolicyRuntimeServices.model_validate(
            control(
                "POST",
                f"/v1/projects/{project}/policy-runtime/services",
                query.model_dump(mode="json"),
            )
        )
        if str(services.project_id) != project or services.scope != query:
            raise ValueError("POLICY_ACTIVITY_SOURCE_OR_INSPECTION_SCOPE_CHANGED")
        if services.truncated or len(services.services) != 1:
            raise ValueError(
                "POLICY_ACTIVITY_SELECT_EXPLICIT_SERVICE_OR_COLLECT_CURRENT_SOURCE_METADATA"
            )
        service_name = services.services[0]
        discovered_environment = services.environment_id
    body = PolicyRuntimeScope(service_name=service_name, **query.model_dump(mode="json"))
    result = PolicyRuntimeSummary.model_validate(
        control(
            "POST", f"/v1/projects/{project}/policy-runtime/inspect", body.model_dump(mode="json")
        )
    )
    if (
        str(result.project_id) != project
        or result.scope != body
        or snapshot(root) != before
        or (discovered_environment is not None and discovered_environment != result.environment_id)
    ):
        raise ValueError("POLICY_ACTIVITY_SOURCE_OR_INSPECTION_SCOPE_CHANGED")
    return dict(
        status="RETAINED_METADATA_ONLY",
        provider_calls=0,
        application_source_changed=False,
        source_binding=source.model_dump(mode="json"),
        summary=result.model_dump(mode="json"),
    )
