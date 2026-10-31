"""Observed source eligibility is a bounded metadata join, never a synthetic hit-rate claim."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, Rounded, Subnormal, localcontext
from uuid import UUID, uuid4

import pytest
from integration.test_native_request_profiles import queue_analysis, retained_profile_stream

from cutback.core.models import LLMEvent
from cutback.core.request_profiles import SourceBinding, fingerprint
from cutback.server.db import CapturePolicyRecord, PayloadSample, StoredEvent
from cutback.server.native_profile_db import NativeProfileAnalysis
from cutback.server.native_profile_service import run_native_profile_one

pytestmark = pytest.mark.postgres


def binding(**changes):
    value = {
        "version": "request-profile-v1",
        "workflow_id": "classify",
        "call_site_id": "callsite:owned-source",
        "source_file": "app.py",
        "source_sha256": "a" * 64,
        "source_commit": "c" * 40,
        "dependencies": {"app.py": "a" * 64},
        "request_sha256": "b" * 64,
        "model_binding": {"file": "app.py", "line": 1, "column": 0, "end_line": 1, "end_column": 5},
        **changes,
    }
    return SourceBinding(**value, profile_sha256=fingerprint(value))


def prepare(api, tenant, tmp_path, monkeypatch, event, *, cost="normal", traffic="application"):
    sample, store, _ = retained_profile_stream(api, tenant, tmp_path, monkeypatch, event)
    source = binding()
    with api[2].state.sessions.begin() as session:
        rows = session.query(StoredEvent).filter_by(project_id=UUID(tenant[0])).all()
        for index, row in enumerate(rows):
            row.event = (
                LLMEvent.model_validate(row.event)
                .model_copy(
                    update={
                        "call_site_id": source.call_site_id,
                        "source_commit": source.source_commit,
                        "traffic_kind": traffic,
                        "usage_source": "estimated" if cost == "estimated" else "provider_reported",
                    }
                )
                .model_dump(mode="json")
            )
            if cost == "unknown" or cost == "partial" and index == 0:
                row.cost_usd = None
            elif cost == "zero":
                row.cost_usd = Decimal(0)
            elif cost == "unpriced_status":
                row.cost_status = "unpriced"
    queued, request = queue_analysis(api, tenant, sample)
    assert queued.status_code == 201
    assert (
        run_native_profile_one(
            api[2].state.sessions, store, only_analysis_id=request["request_id"]
        )["status"]
        == "COMPLETED"
    )
    body = {
        "source_binding": source.model_dump(mode="json"),
        "baseline_model": event.requested_model,
        "cache_execution": {
            "freshness_ref": "owned-immutable-v1",
            "ttl_seconds": 300,
            "content_retention_authorized": True,
            "immutable_or_safely_invalidated": True,
            "operation_cost_upper_usd": "0.000000000001",
            "operation_cost_basis_ref": "owned-file-cache-bound",
        },
    }
    path = (
        f"/v1/projects/{tenant[0]}/native-request-profiles/analyses/"
        f"{request['request_id']}/cache-trial"
    )
    return body, path, store, request


def test_source_matched_observation_supports_only_separate_trial_with_no_storage_read(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    event,
):
    body, path, store, _ = prepare(api, tenant, tmp_path, monkeypatch, event)
    monkeypatch.setattr(
        store, "get", lambda *args: pytest.fail("METADATA_ASSESSMENT_MUST_NOT_READ_CONTENT")
    )
    response = api[0].post(path, headers=api[1], json=body)
    assert response.status_code == 200
    result = response.json()
    assert result["decision"] == "prepare_separate_cache_trial" and result["reasons"] == []
    assert result["profiled_attempts"] == 2 and result["modeled_potential_hits"] == 1
    assert result["declared_cache_operations"] == 3
    assert Decimal(result["modeled_net_native_window_opportunity_usd"]) > 0
    assert result["whole_task_cost_usd"] is None and result["realized_savings_usd"] is None
    assert result["source_change_authorized"] is False and result["paid_calls_authorized"] is False
    assert result["encrypted_storage_reads"] == result["provider_calls"] == 0
    assert result["cache_delivery_benchmarked"] is False
    expected = result.pop("assessment_sha256")
    assert fingerprint(result) == expected
    assert "request_hmac_sha256" not in response.text
    assert api[0].post(path, headers=tenant[2], json=body).status_code == 403
    assert api[0].post(path, headers=tenant[1], json=body).status_code == 403
    other = api[0].post("/v1/projects", headers=api[1], json={"slug": "other-" + uuid4().hex})
    assert other.status_code == 201
    foreign = path.replace(tenant[0], other.json()["id"])
    assert api[0].post(foreign, headers=api[1], json=body).status_code == 404


@pytest.mark.parametrize(
    "mismatch", ["callsite", "commit", "unknown_commit", "model", "ttl", "freshness"]
)
def test_incompatible_source_and_context_never_qualify_from_workflow_name_alone(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    event,
    mismatch,
):
    body, path, _, _ = prepare(api, tenant, tmp_path, monkeypatch, event)
    if mismatch == "callsite":
        body["source_binding"] = binding(call_site_id="callsite:another").model_dump(mode="json")
    elif mismatch == "commit":
        body["source_binding"] = binding(source_commit="d" * 40).model_dump(mode="json")
    elif mismatch == "unknown_commit":
        body["source_binding"] = binding(source_commit=None).model_dump(mode="json")
    elif mismatch == "model":
        body["baseline_model"] = "gpt-4.1"
    elif mismatch == "ttl":
        body["cache_execution"]["ttl_seconds"] = 60
    else:
        body["cache_execution"]["freshness_ref"] = "changed-context"
    response = api[0].post(path, headers=api[1], json=body)
    assert response.status_code == 200
    assert response.json()["decision"] == "abstain" and response.json()["reasons"]


@pytest.mark.parametrize(
    "cost", ["unknown", "partial", "zero", "high_cache", "estimated", "unpriced_status"]
)
def test_unknown_or_unprofitable_native_economics_are_not_filled_with_zero(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    event,
    cost,
):
    body, path, _, _ = prepare(
        api,
        tenant,
        tmp_path,
        monkeypatch,
        event,
        cost=cost if cost != "high_cache" else "normal",
    )
    if cost == "high_cache":
        body["cache_execution"]["operation_cost_upper_usd"] = "1"
    elif cost == "zero":
        body["cache_execution"]["operation_cost_upper_usd"] = "0"
    result = api[0].post(path, headers=api[1], json=body).json()
    assert result["decision"] == "abstain"
    if cost in {"unknown", "partial", "estimated", "unpriced_status"}:
        assert result["provider_modeled_window_cost_usd"] is None
        assert result["modeled_net_native_window_opportunity_usd"] is None
    else:
        assert Decimal(result["modeled_net_native_window_opportunity_usd"]) <= 0
        assert "NO_POSITIVE_MODELED_NATIVE_WINDOW_OPPORTUNITY" in result["reasons"]


@pytest.mark.parametrize("change", ["missing_metadata", "price_change", "missing_digest"])
def test_missing_or_changed_retained_evidence_does_not_inherit_a_previous_qualification(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    event,
    change,
):
    body, path, _, request = prepare(api, tenant, tmp_path, monkeypatch, event)
    if change == "missing_digest":
        monkeypatch.setattr(
            "cutback.server.cache_workload_api.retained_metadata",
            lambda *a, **k: pytest.fail("LEGACY_ANALYSIS_MUST_NOT_RESCAN_METADATA"),
        )
    with api[2].state.sessions.begin() as session:
        if change == "missing_digest":
            row = session.get(NativeProfileAnalysis, UUID(request["request_id"]))
            row.summary = {
                key: value
                for key, value in row.summary.items()
                if key != "metadata_evidence_sha256"
            }
        else:
            row = session.query(StoredEvent).filter_by(project_id=UUID(tenant[0])).first()
            if change == "missing_metadata":
                session.delete(row)
            else:
                row.cost_usd += Decimal("0.001")
    result = api[0].post(path, headers=api[1], json=body).json()
    assert result["decision"] == "abstain"
    assert "RETAINED_METADATA_SNAPSHOT_UNAVAILABLE_OR_CHANGED" in result["reasons"]
    assert result["provider_modeled_window_cost_usd"] is None
    assert result["modeled_avoided_provider_cost_usd"] is None


def test_current_capture_authority_and_application_traffic_remain_required(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    event,
):
    body, path, _, request = prepare(
        api, tenant, tmp_path, monkeypatch, event, traffic="evaluation"
    )
    result = api[0].post(path, headers=api[1], json=body).json()
    assert "APPLICATION_ATTEMPT_SCOPE_UNQUALIFIED" in result["reasons"]
    assert "SOURCE_MATCHED_PROVIDER_COST_COVERAGE_UNKNOWN" not in result["reasons"]
    with api[2].state.sessions.begin() as session:
        analysis = session.get(NativeProfileAnalysis, UUID(request["request_id"]))
        session.get(CapturePolicyRecord, analysis.policy_id).enabled = False
    assert api[0].post(path, headers=api[1], json=body).status_code == 403


def test_late_canonical_attempt_invalidates_prior_census(api, tenant, tmp_path, monkeypatch, event):
    body, path, _, _ = prepare(api, tenant, tmp_path, monkeypatch, event)
    with api[2].state.sessions() as session:
        row = session.query(StoredEvent).filter_by(project_id=UUID(tenant[0])).first()
        late = LLMEvent.model_validate(row.event).model_copy(update={"event_id": uuid4()})
    added = api[0].post(
        "/v1/ingest/events",
        headers=tenant[1],
        json={
            "project_id": tenant[0],
            "events": [late.model_dump(mode="json")],
        },
    )
    assert added.status_code == 200
    result = api[0].post(path, headers=api[1], json=body).json()
    assert result["decision"] == "abstain"
    assert "RETAINED_METADATA_SNAPSHOT_UNAVAILABLE_OR_CHANGED" in result["reasons"]


def test_bounded_metadata_page_cannot_be_reported_as_a_complete_window_cost(
    api, tenant, tmp_path, monkeypatch, event
):
    body, path, _, _ = prepare(api, tenant, tmp_path, monkeypatch, event)
    with api[2].state.sessions() as session:
        row = session.query(StoredEvent).filter_by(project_id=UUID(tenant[0])).first()
        template = LLMEvent.model_validate(row.event)
    for _ in range(5):
        events = [
            template.model_copy(
                update={
                    "event_id": uuid4(),
                    "request_attempt_id": uuid4(),
                    "span_id": uuid4().hex[:16],
                }
            ).model_dump(mode="json")
            for _ in range(200)
        ]
        response = api[0].post(
            "/v1/ingest/events",
            headers=tenant[1],
            json={
                "project_id": tenant[0],
                "events": events,
            },
        )
        assert response.status_code == 200
    result = api[0].post(path, headers=api[1], json=body).json()
    assert result["decision"] == "abstain"
    assert "RETAINED_METADATA_SNAPSHOT_UNAVAILABLE_OR_CHANGED" in result["reasons"]
    assert result["provider_modeled_window_cost_usd"] is None
    assert result["modeled_avoided_provider_cost_usd"] is None
    assert result["modeled_net_native_window_opportunity_usd"] is None


def test_window_cost_sum_is_independent_of_the_callers_decimal_context(
    api, tenant, tmp_path, monkeypatch, event
):
    from cutback.server.cache_workload_api import CacheWorkloadInput, assess_cache_workload

    event = event.model_copy(
        update={"input_tokens": 1234567, "output_tokens": 137, "cache_read_tokens": 0}
    )
    body, _, _, request = prepare(api, tenant, tmp_path, monkeypatch, event)
    typed = CacheWorkloadInput.model_validate(body)
    with api[2].state.sessions() as session:
        row = session.get(NativeProfileAnalysis, UUID(request["request_id"]))
        with localcontext() as context:
            context.prec = 64
            expected = sum(
                (
                    item.cost_usd
                    for item in session.query(StoredEvent).filter_by(project_id=UUID(tenant[0]))
                ),
                Decimal(0),
            )
        with localcontext() as context:
            context.prec = 2
            context.Emin, context.Emax = -1, 1
            context.traps[Subnormal] = context.traps[Rounded] = True
            result = assess_cache_workload(session, row, typed)
        assert result.decision == "prepare_separate_cache_trial"
        assert Decimal(result.provider_modeled_window_cost_usd) == expected


def test_summary_expires_no_later_than_its_encrypted_source(
    api, tenant, tmp_path, monkeypatch, event
):
    sample, store, _ = retained_profile_stream(api, tenant, tmp_path, monkeypatch, event)
    deadline = datetime.now(UTC) + timedelta(minutes=10)
    with api[2].state.sessions.begin() as session:
        for row in session.query(PayloadSample).filter_by(project_id=UUID(tenant[0])):
            row.expires_at = deadline
    queued, request = queue_analysis(api, tenant, sample)
    assert queued.status_code == 201
    result = run_native_profile_one(
        api[2].state.sessions, store, only_analysis_id=request["request_id"]
    )
    assert result["status"] == "COMPLETED"
    with api[2].state.sessions() as session:
        row = session.get(NativeProfileAnalysis, UUID(request["request_id"]))
        assert row.expires_at == deadline
