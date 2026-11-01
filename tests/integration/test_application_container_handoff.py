"""Native synthetic SDK spending, current PostgreSQL rights and actual Docker checks."""

import pytest
from integration import test_agent_source_policy_handoff as policy
from integration import test_campaign_handoff as campaign
from support_application_container import container_image as container_image

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("revoke_after_checks", [False, True])
def test_real_container_campaign_handoff_refreshes_rights_and_retains_no_replay(
    api, tenant, tmp_path, monkeypatch, postgres_url, container_image, revoke_after_checks
):
    campaign.test_native_campaign_cli_finish_refreshes_rights_without_repeating_native_spend(
        api,
        tenant,
        tmp_path,
        monkeypatch,
        postgres_url,
        revoke_after_checks,
        container=container_image,
    )


@pytest.mark.parametrize("revoke_after_checks", [False, True])
def test_source_policy_container_handoff_checks_both_trees_and_refuses_revoked_rights(
    api, tenant, tmp_path, monkeypatch, container_image, revoke_after_checks
):
    policy.test_http_policy_handoff_binds_source_and_refreshes_current_rights(
        api,
        tenant,
        tmp_path,
        monkeypatch,
        revoke_after_checks,
        current_routed=True,
        async_consumer=False,
        container=container_image.model_dump(mode="json"),
    )
