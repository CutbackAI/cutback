import json
from uuid import uuid4

import httpx
import pytest
from support_evaluation import plan_for
from support_isolated_tools import record_case

from cutback.core.isolated_episodes import IsolatedRecordScenario
from cutback.core.prompts import validate_native_schema
from cutback.evals.datasets import Case
from cutback.evals.experiments import ExperimentPlan, plan_summary, run
from cutback.evals.isolated_tools import record_tool_definitions
from cutback.tool_broker_boot import ToolBrokerConfig
from cutback.tool_client import ToolBrokerClient, ToolBrokerUncertain, broker_origin


def isolated_plan(project, principal, broker, image, **changes):
    return plan_for(
        {"immutable_hash": "a" * 64},
        baseline={"key": "baseline", "provider": "openai", "model": "gpt-4.1-mini"},
        candidates=[{"key": "candidate", "provider": "openai", "model": "gpt-4.1-mini"}],
        approved_providers=["openai"],
        metric={"kind": "episode_contract", "minimum": 1},
        evaluator_version="deterministic-v8",
        content_nonce=str(uuid4()),
        episode_execution={
            "version": "native-isolated-record-episode-v1",
            "max_turns": 4,
            "max_tool_calls": 3,
            "isolated_tools": {
                "project_id": str(project),
                "principal_id": str(principal),
                "broker_id": str(broker),
                "image_id": image,
                "environment": "staging",
                "content_use_approved": True,
            },
        },
        **changes,
    )


def test_isolated_contract_cannot_be_synthetic_fixture_or_execute_without_boundary():
    project, principal, broker = uuid4(), uuid4(), uuid4()
    case = record_case(project, principal)
    assert isinstance(Case.model_validate(case).episode, IsolatedRecordScenario)
    plan = isolated_plan(project, principal, broker, "sha256:" + "a" * 64)
    dataset = {
        "immutable_hash": "a" * 64,
        "items": [{"case": case, "split": "test", "group_hash": "independent-owned"}],
    }
    summary = plan_summary(plan, dataset)
    result = run(plan, dataset, object(), approval_hash=summary["plan_hash"])
    assert result["status"] == "FAILED" and result["requests"] == 0
    raw = plan.model_dump(mode="json")
    for change in ({"evaluator_version": "deterministic-v4"}, {"content_nonce": None}):
        with pytest.raises(ValueError):
            ExperimentPlan.model_validate({**raw, **change})
    raw["episode_execution"]["isolated_tools"]["max_sessions"] = 1
    with pytest.raises(ValueError, match="FORECAST"):
        plan_summary(ExperimentPlan.model_validate(raw), dataset)
    for surface in ("responses.create", "chat.completions.create", "messages.create"):
        for tool in record_tool_definitions(write=True):
            validate_native_schema(tool["parameters"], surface)
    assert "seeds" not in json.dumps(record_tool_definitions(write=True))


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com",
        "https://user:password@example.com",
        "https://example.com/path",
        "https://example.com#fragment",
        "https://[",
        "https://example.com:0",
        "https://example.com\n",
        None,
    ],
)
def test_broker_origin_rejects_ambient_or_unencrypted_destination(origin):
    with pytest.raises(ValueError, match="FIXED_TLS_ORIGIN"):
        broker_origin(origin, allow_insecure_loopback=True)
    assert (
        broker_origin("http://127.0.0.1:8091", allow_insecure_loopback=True)
        == "http://127.0.0.1:8091"
    )


def test_client_lost_operation_acknowledgement_is_not_retried_or_reflected():
    from unit.test_isolated_records import records

    temporary, policy, *_ = records()
    temporary.close()
    calls = []

    def lose(request):
        calls.append(request)
        raise httpx.ReadError("SYNTHETIC_HIDDEN_TRANSPORT_DETAIL")

    client = ToolBrokerClient(
        "https://owned-broker.invalid",
        "x" * 48,
        project_id=policy.project_id,
        principal_id=policy.principal_id,
        broker_id=uuid4(),
        image_id=policy.image_id,
        transport=httpx.MockTransport(lose),
    )
    try:
        with pytest.raises(ToolBrokerUncertain) as failure:
            client.execute(policy, uuid4(), "records_read", {})
        assert len(calls) == 1 and "SYNTHETIC_HIDDEN" not in str(failure.value)
    finally:
        client.close()


def test_broker_boot_requires_tls_or_explicit_loopback_and_stable_id():
    from datetime import UTC, datetime, timedelta

    raw = {
        "broker_id": str(uuid4()),
        "image_id": "sha256:" + "a" * 64,
        "clients": [
            {
                "project_id": str(uuid4()),
                "principal_id": str(uuid4()),
                "token_sha256": "b" * 64,
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            }
        ],
    }
    with pytest.raises(ValueError):
        ToolBrokerConfig.model_validate(raw)
    ToolBrokerConfig.model_validate({**raw, "allow_insecure_loopback": True})
    with pytest.raises(ValueError):
        ToolBrokerConfig.model_validate({**raw, "allow_insecure_loopback": True, "host": "0.0.0.0"})
