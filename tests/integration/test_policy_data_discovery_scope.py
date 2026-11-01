"""Real PostgreSQL metadata selection retains tenant, rights and approval boundaries."""

import json
from uuid import UUID, uuid4

import httpx
import pytest
from integration.test_source_policy_program_api import prepared_program
from support_http import owned_collector
from support_policy_application import application

from cutback.agent_control import Control
from cutback.agent_policy_data import discover, verify_baseline
from cutback.agent_policy_program import prepare
from cutback.policy_source import inspect_configuration
from cutback.server.db import CapturePolicyRecord

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("revoked", [False, True])
def test_metadata_selection_never_bypasses_actual_content_rights_or_approval(
    api, tenant, tmp_path, monkeypatch, revoked
):
    root = application(tmp_path)
    _, binding = inspect_configuration(root, "app.py")
    base, _, dataset, policy, executor, calls, _, _, request = prepared_program(
        api, tenant, tmp_path, monkeypatch, source_binding=binding
    )
    try:
        with owned_collector(api[2]) as endpoint, httpx.Client(trust_env=False) as client:
            control = Control(endpoint, api[1]["Authorization"][7:], client=client)
            metadata = discover(root, control, tenant[0], producer_file="app.py", fixture=True)
            assert metadata["selected_dataset_id"] == dataset["id"]
            assert metadata["selected_baseline_snapshot_id"] == request["baseline_snapshot_id"]
            verify_baseline(control, tenant[0], request["baseline_snapshot_id"], metadata)
            other = api[0].post(
                "/v1/projects", headers=api[1], json={"slug": "other-" + uuid4().hex}
            )
            assert other.status_code == 201
            assert (
                api[0]
                .get(
                    base.replace(tenant[0], other.json()["id"]) + "/datasets/" + dataset["id"],
                    headers=api[1],
                )
                .status_code
                == 404
            )
            viewer = Control(endpoint, tenant[2]["Authorization"][7:], client=client)
            with pytest.raises(ValueError):
                discover(root, viewer, other.json()["id"], producer_file="app.py")
            if revoked:
                with api[2].state.sessions.begin() as session:
                    session.get(CapturePolicyRecord, UUID(policy)).enabled = False
            options = dict(
                producer_file="app.py",
                purpose="owned-auto-data",
                max_usd="100",
                generate=False,
                fixture_transport=True,
                min_test_samples=3,
                min_test_groups=3,
                minimum=1,
                savings_per_success="0.01",
                forecast_successful_tasks=100000,
            )
            if revoked:
                with pytest.raises(ValueError):
                    prepare(root, control, tenant[0], **options)
            else:
                row = prepare(root, control, tenant[0], **options)
                assert row["status"] == "READY_FOR_APPROVAL"
                assert row["local_preparation_data_selection"] == metadata
                again = prepare(root, control, tenant[0], **options)
                assert again["id"] == row["id"] and again["program_hash"] == row["program_hash"]
            assert calls == []
            saved = json.loads((root / ".cutback/agent/policy-program.json").read_text())
            assert (
                "approval_ref" not in saved
                and "OWNED_PRIVATE_POLICY_INSTRUCTION" not in json.dumps(saved)
            )
    finally:
        executor.close()
