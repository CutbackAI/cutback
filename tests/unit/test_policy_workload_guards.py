"""Policy observation metadata cannot become an edit span or execution approval."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from support_policy_application import application
from unit.test_cache_workload_contract import assessment

from cutback.agent_policy_workload import assess, source_input
from cutback.core.cache_workload import POLICY_WORKLOAD_CONTRACT
from cutback.core.request_profiles import SourceBinding, fingerprint


def contract():
    return {
        "freshness_ref": "owned-immutable-v1",
        "ttl_seconds": 300,
        "content_retention_authorized": True,
        "immutable_or_safely_invalidated": True,
        "operation_cost_upper_usd": "0.000000000001",
        "operation_cost_basis_ref": "owned-operation-bound",
    }


def remote(*, mutate=None, change=None):
    identifier, calls = str(uuid4()), []

    def control(method, path, body=None):
        calls.append((method, path))
        if path.endswith("/ready"):
            return {"source_policy_workload_contract": POLICY_WORKLOAD_CONTRACT}
        assert method == "POST" and path.endswith("/source-policy-cache-trial")
        if mutate:
            mutate()
        value = assessment(
            project_id=path.split("/")[3],
            workflow=body["source_binding"]["source"]["workflow"],
            analysis_id=identifier,
            source_binding_sha256=fingerprint(body["source_binding"]),
            cache_execution_sha256=fingerprint(body["cache_execution"]),
        )
        if change:
            change(value)
            value["assessment_sha256"] = fingerprint(
                {key: item for key, item in value.items() if key != "assessment_sha256"}
            )
        return value

    return control, identifier, calls


def test_policy_attribution_is_content_free_and_not_a_native_edit_span(tmp_path):
    root = application(tmp_path)
    body, source = source_input(root, producer_file="app.py", cache_execution=contract())
    assert body["source_binding"]["source"]["commit"] == source.commit
    assert "OWNED_PRIVATE_POLICY_INSTRUCTION" not in json.dumps(body)
    with pytest.raises(ValueError):
        SourceBinding.model_validate(body["source_binding"])
    control, identifier, calls = remote()
    result = assess(
        root,
        control,
        uuid4(),
        producer_file="app.py",
        cache_execution=contract(),
        analysis_id=identifier,
    )
    assert len(calls) == 2 and result["decision"] == "prepare_separate_cache_trial"
    assert result["source_change_authorized"] is result["paid_calls_authorized"] is False
    assert result["production_hit_rate"] is result["realized_savings_usd"] is None
    assert result["encrypted_storage_reads"] == result["provider_calls"] == 0
    saved = root / ".cutback/agent/policy-workload.json"
    assert json.loads(saved.read_text("utf-8")) == result
    assert not (saved.parent / "policy-program.json").exists()


@pytest.mark.parametrize(
    "kind",
    ["project", "environment", "workflow", "source", "cache", "model", "expired", "authority"],
)
def test_forged_or_expired_policy_assessment_never_journals(tmp_path, kind):
    root = application(tmp_path)

    def change(value):
        fields = {
            "project": ("project_id", str(uuid4())),
            "environment": ("environment", "production"),
            "workflow": ("workflow", "other-workflow"),
            "source": ("source_binding_sha256", "0" * 64),
            "cache": ("cache_execution_sha256", "0" * 64),
            "model": ("baseline_model", "gpt-4.1-mini"),
            "expired": ("valid_until", (datetime.now(UTC) - timedelta(seconds=1)).isoformat()),
            "authority": ("paid_calls_authorized", True),
        }
        field, changed = fields[kind]
        value[field] = changed

    control, identifier, _ = remote(change=change)
    with pytest.raises(ValueError):
        assess(
            root,
            control,
            uuid4(),
            producer_file="app.py",
            cache_execution=contract(),
            analysis_id=identifier,
        )
    assert not (root / ".cutback/agent/policy-workload.json").exists()


def test_source_change_during_assessment_cannot_save_stale_recommendation(tmp_path):
    root = application(tmp_path)
    path = root / "policy.json"
    control, identifier, _ = remote(mutate=lambda: path.write_bytes(path.read_bytes() + b"\n"))
    with pytest.raises(ValueError):
        assess(
            root,
            control,
            uuid4(),
            producer_file="app.py",
            cache_execution=contract(),
            analysis_id=identifier,
        )
    assert not (root / ".cutback/agent/policy-workload.json").exists()


@pytest.mark.parametrize("options", [{"async_consumer": True}, {"current_routed": True}])
def test_unqualified_request_profile_surfaces_fail_before_http(tmp_path, options):
    root = application(tmp_path, **options)
    with pytest.raises(ValueError, match="UNCACHED_SYNC_NATIVE_REFERENCE_REQUIRED"):
        assess(
            root,
            lambda *args: pytest.fail("NO_HTTP"),
            uuid4(),
            producer_file="app.py",
            cache_execution=contract(),
        )


def test_empty_and_oversized_pages_and_old_collectors_do_not_queue_work(tmp_path):
    root = application(tmp_path)
    args = {"producer_file": "app.py", "cache_execution": contract()}
    with pytest.raises(ValueError, match="UPGRADE_REQUIRED"):
        assess(root, lambda *args: {}, uuid4(), **args)
    for page in ([], [{}] * 101):
        calls = []

        def control(method, path, body=None, *, calls=calls, page=page):
            calls.append(method)
            return (
                {"source_policy_workload_contract": POLICY_WORKLOAD_CONTRACT}
                if (path.endswith("/ready"))
                else page
            )

        if page:
            with pytest.raises(ValueError, match="BOUNDED_ANALYSIS_PAGE_REQUIRED"):
                assess(root, control, uuid4(), **args)
        else:
            assert assess(root, control, uuid4(), **args)["decision"] == "abstain"
        assert calls == ["GET", "GET"]
