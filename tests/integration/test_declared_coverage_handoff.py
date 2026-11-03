"""Native synthetic campaign -> source-bound coverage draft -> real local checks."""

import pytest
from test_campaign_handoff import (
    test_native_campaign_cli_finish_refreshes_rights_without_repeating_native_spend as run_handoff,
)

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("revoke", [False, True])
def test_cli_uses_generated_coverage_after_review_without_repeating_native_calls(
    api, tenant, tmp_path, monkeypatch, postgres_url, revoke
):
    run_handoff(
        api,
        tenant,
        tmp_path,
        monkeypatch,
        postgres_url,
        revoke_after_checks=revoke,
        declared_mapping=True,
    )
