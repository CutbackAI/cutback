"""Automatic workload guidance remains separate from an unchanged approval envelope."""

import json
from uuid import uuid4

import pytest
from support_policy_application import application
from unit.test_cache_workload_contract import assessment
from unit.test_policy_workload_guards import contract

from cutback.agent_policy_program import prepare
from cutback.core.cache_workload import POLICY_WORKLOAD_CONTRACT
from cutback.core.request_profiles import fingerprint


class StopAtContentUpload(Exception):
    pass


@pytest.mark.parametrize("eligible,fixture", [(False, False), (True, False), (False, True)])
def test_prepare_automatically_collects_only_scoped_metadata_before_upload_or_approval(
    tmp_path, eligible, fixture
):
    root = application(tmp_path)
    dataset, project, analysis = str(uuid4()), str(uuid4()), str(uuid4())
    calls = []

    class Remote:
        endpoint = "https://collector.example.invalid"

        def __call__(self, method, path, body=None):
            calls.append((method, path))
            if path.endswith("/ready"):
                return {
                    "source_policy_program_contract": "cutback.source-policy-program.v1",
                    "source_policy_workload_contract": POLICY_WORKLOAD_CONTRACT,
                }
            if path.endswith("/datasets/" + dataset):
                return {
                    "id": dataset,
                    "hash": "a" * 64,
                    "workflow": "routing-task",
                    "split_counts": {"train": 20},
                    "provenance": {"source_type": "synthetic_fixture"},
                }
            if method == "GET":
                assert "native-request-profiles/analyses?limit=100&offset=0" in path
                return (
                    [
                        {
                            "id": analysis,
                            "environment": "staging",
                            "workflow": "routing-task",
                            "state": "COMPLETED",
                            "summary": {"cache_trial_eligible": True},
                        }
                    ]
                    if (eligible)
                    else []
                )
            if path.endswith("/source-policy-cache-trial"):
                assert "OWNED_PRIVATE_POLICY_INSTRUCTION" not in json.dumps(body)
                return assessment(
                    project_id=project,
                    analysis_id=analysis,
                    workflow="routing-task",
                    source_binding_sha256=fingerprint(body["source_binding"]),
                    cache_execution_sha256=fingerprint(body["cache_execution"]),
                )
            assert path.endswith("/source-policies")
            raise StopAtContentUpload

    with pytest.raises(StopAtContentUpload):
        prepare(
            root,
            Remote(),
            project,
            dataset_id=dataset,
            baseline_snapshot_id=uuid4(),
            producer_file="app.py",
            purpose="owned-cache-guidance",
            max_usd="1",
            cache_contract=contract(),
            generate=False,
            fixture_transport=fixture,
        )
    saved = json.loads((root / ".cutback/agent/policy-program.json").read_text("utf-8"))
    guidance = saved["preparation_workload_assessment"]
    assert guidance["decision"] == ("prepare_separate_cache_trial" if eligible else "abstain")
    assert guidance["source_change_authorized"] is guidance["paid_calls_authorized"] is False
    assert guidance["provider_calls"] == guidance["encrypted_storage_reads"] == 0
    assert guidance["production_hit_rate"] is guidance["realized_savings_usd"] is None
    assert "preparation_workload_assessment" not in saved["intent"]
    assert ("source-exact-cache" in saved["intent"]["candidate_keys"]) is (eligible or fixture)
    assert saved["preparation_cache_control_decision"]["included_in_preview"] is (
        eligible or fixture
    )
    assert "program_id" not in saved and "approval_ref" not in saved
    assert "OWNED_PRIVATE_POLICY_INSTRUCTION" not in json.dumps(saved)
    assert all("approve" not in path and "/runs" not in path for _, path in calls)


@pytest.mark.parametrize("value", [1, "true", "false"])
def test_fixture_transport_cannot_be_enabled_by_a_truthy_non_boolean(tmp_path, value):
    root = application(tmp_path)
    with pytest.raises(ValueError, match="EXPLICIT_TRANSPORT_BOOLEAN_REQUIRED"):
        prepare(
            root,
            lambda *args: pytest.fail("NO_HTTP"),
            uuid4(),
            dataset_id=uuid4(),
            baseline_snapshot_id=uuid4(),
            producer_file="app.py",
            purpose="owned-explicit-transport",
            max_usd="1",
            fixture_transport=value,
        )
