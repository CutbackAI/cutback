"""Agent retention is an explicit frozen choice, with metadata as the default."""

from uuid import UUID

import pytest
from support_agent import prepare_fresh

from cutback.agent import prepare
from cutback.server.db import ExperimentPlanRecord
from cutback.server.plan_artifacts import load_plan

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("retain", [False, True])
def test_agent_case_retention_is_opt_in_and_cannot_be_added_to_an_existing_approved_scope(
    api, tenant, tmp_path, monkeypatch, retain
):
    root, control, state, arguments = prepare_fresh(
        api, tenant, tmp_path, monkeypatch, for_campaign=True, retain_case_outputs=retain
    )
    with api[2].state.sessions() as session:
        row = session.get(ExperimentPlanRecord, UUID(state["plan"]["id"]))
        assert row.specification["public_specification"]["retain_case_outputs"] is retain
        plan = load_plan(api[2].state.artifact_store, UUID(tenant[0]), row.specification)
        assert plan.retain_case_outputs is retain
    prepare(root, control, tenant[0], **arguments)
    with pytest.raises(ValueError, match="FROZEN_PLAN_CHANGED"):
        prepare(root, control, tenant[0], **{**arguments, "retain_case_outputs": not retain})
