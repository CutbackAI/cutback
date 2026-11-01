"""Actual container checks inside the durable automatic source handoff."""

import pytest
from support_application_container import container_image as container_image
from test_agent_campaign_handoff_controls import coverage, invoke
from test_agent_campaign_handoff_controls import journey as journey
from test_agent_routing_handoff_controls import invoke as routing_invoke
from test_agent_routing_handoff_controls import setup_journey

import cutback.agent_campaign_handoff as handoff
from cutback.application_checks import snapshot
from cutback.core.workspace import read_private


@pytest.fixture
def routing_journey(tmp_path, monkeypatch):
    return setup_journey(tmp_path, monkeypatch)


def test_container_handoff_checks_both_trees_and_never_repeats_a_completed_dispatch(
    journey, container_image, monkeypatch
):
    source = snapshot(journey[0])
    options = {"container": container_image.model_dump(mode="json")}
    preview = invoke(journey, **options)
    contract = read_private(journey[1] / "contract.json")
    assert contract["schema_version"] == "cutback.application-check.v2"
    assert preview["status"] == "CHECKS_READY" and not preview["application_checks_executed"]
    with pytest.raises(ValueError, match="FROZEN_INTENT_CHANGED"):
        invoke(journey)
    result = invoke(journey, **options, coverage=coverage(preview), execute_local=True)
    assert result["status"] == "REVIEWED" and result["both_application_trees_checked"]
    receipt = read_private(journey[1] / "checks.json")
    assert receipt["isolation"] == "local_linux_container_network_none"
    assert receipt["all_passed"] and snapshot(journey[0]) == source

    def no_replay(*args, **kwargs):
        pytest.fail("completed application check dispatch was replayed")

    monkeypatch.setattr(handoff, "run_checks", no_replay)
    assert invoke(journey, **options)["application_checks_executed"] is True
    assert invoke(journey, **options, execute_local=True)["status"] == "REVIEWED"


def test_lost_container_check_reply_does_not_authorize_a_second_execution(
    journey, container_image, monkeypatch
):
    options = {"container": container_image.model_dump(mode="json")}
    preview = invoke(journey, **options)
    real, calls = handoff.run_checks, []

    def lost(*args, **kwargs):
        receipt = real(*args, **kwargs)
        assert receipt["all_passed"]
        calls.append(True)
        raise ValueError("OWNED_CHECK_REPLY_LOST")

    monkeypatch.setattr(handoff, "run_checks", lost)
    with pytest.raises(ValueError, match="OWNED_CHECK_REPLY_LOST"):
        invoke(journey, **options, coverage=coverage(preview), execute_local=True)
    with pytest.raises(ValueError, match="UNCERTAIN_NO_AUTOMATIC_REPLAY"):
        invoke(journey, **options, execute_local=True)
    assert calls == [True] and not (journey[1] / "review.json").exists()


def test_routing_handoff_retains_exact_container_intent_and_verified_inverse(
    routing_journey, container_image
):
    source = snapshot(routing_journey[0])
    options = {"container": container_image.model_dump(mode="json")}
    preview = routing_invoke(routing_journey, **options)
    result = routing_invoke(
        routing_journey,
        **options,
        coverage={"ticket-priority": preview["command_keys"]},
        execute_local=True,
    )
    assert result["status"] == "REVIEWED" and result["byte_exact_rollback_checked"]
    assert snapshot(routing_journey[0]) == source
    receipt = read_private(routing_journey[1] / "checks.json")
    assert receipt["all_passed"] and receipt["isolation"] == "local_linux_container_network_none"
