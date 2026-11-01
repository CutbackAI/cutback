"""Bounded service discovery retains exact source, environment and project scope."""

import pytest
from integration.test_policy_activity_scope import event, intake, scope_body

pytestmark = pytest.mark.postgres


def query(api, tenant, body, headers=None):
    return api[0].post(
        f"/v1/projects/{tenant[0]}/policy-runtime/services", headers=headers or tenant[2], json=body
    )


def test_services_are_distinct_metadata_only_and_match_every_source_filter(api, tenant):
    body = scope_body()
    body.pop("service_name")
    assert query(api, tenant, body).json()["services"] == []
    assert (
        intake(api, tenant, event(), event(), event(service_name="other-owned-service")).status_code
        == 200
    )
    assert (
        intake(api, tenant, event(source_commit="f" * 40, service_name="unrelated")).status_code
        == 200
    )
    response = query(api, tenant, body)
    assert response.status_code == 200
    assert response.json()["services"] == ["other-owned-service", "owned-desk"]
    assert response.json()["project_id"] == tenant[0] and not response.json()["truncated"]
    assert response.json()["capture_coverage"] == "unknown"
    for name, value in (
        ("workflow", "other"),
        ("call_site_id", "other"),
        ("policy_sha256", "f" * 64),
    ):
        assert query(api, tenant, {**body, name: value}).json()["services"] == []
    assert query(api, tenant, body, headers=tenant[1]).status_code == 403
    assert query(api, tenant, {**body, "environment": "other"}).status_code == 404


def test_more_than_twenty_distinct_services_are_explicitly_truncated(api, tenant):
    body = scope_body()
    body.pop("service_name")
    assert (
        intake(
            api, tenant, *(event(service_name=f"owned-{index:02d}") for index in range(21))
        ).status_code
        == 200
    )
    result = query(api, tenant, body).json()
    assert len(result["services"]) == 20 and result["truncated"]
    assert result["services"] == sorted(result["services"])
