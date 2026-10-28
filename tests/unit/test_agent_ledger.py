from decimal import Decimal
from uuid import uuid4

import pytest
from test_agent_turns import frame

from cutback.evals.executors import ConfiguredExecutor
from cutback.evals.experiments import Budget, BudgetLedger, Execution, ModelSpec


def model(**changes):
    return ModelSpec(
        key="owned-turn",
        provider="openai",
        model="gpt-4.1-mini",
        **changes,
    ).with_agent_turn(frame("responses.create"))


def test_unqualified_executor_and_input_bounds_fail_before_any_reservation_or_network():
    reserved = []
    ledger = BudgetLedger(Budget(max_usd="1", max_requests=2), reserve=reserved.append)
    with pytest.raises(ValueError, match="QUALIFIED_EXECUTOR"):
        ledger.call(ConfiguredExecutor(), model(), "")
    assert not reserved and ledger.requests == 0

    class Forbidden:
        supports_agent_turns = True

        def execute(self, *args):
            pytest.fail("An over-bound request reached the executor")

    with pytest.raises(ValueError, match="INPUT_EXCEEDS"):
        ledger.call(Forbidden(), model(max_input_tokens=100), "")
    assert not reserved and ledger.requests == 0


def test_call_receipt_is_reserved_before_execution_and_known_response_is_recorded_once():
    order = []
    identifier = uuid4()

    def reserve(amount, target, purpose):
        assert amount > 0 and target.agent_turn and purpose == "evaluation"
        order.append("reserved")
        return identifier

    def record(call_id, result, status):
        assert call_id == identifier and result.cost_usd == Decimal("0.001")
        order.append(status)

    class Native:
        supports_agent_turns = True

        def execute(self, *args):
            assert order == ["reserved"]
            order.append("executed")
            return Execution("owned", Decimal("0.001"), 1)

    ledger = BudgetLedger(
        Budget(max_usd="1", max_requests=2), reserve_call=reserve, record_call=record
    )
    ledger.call(Native(), model(), "")
    assert order == ["reserved", "executed", "completed"]
    assert ledger.billing_complete and ledger.spent == Decimal("0.001")


@pytest.mark.parametrize("interruption", [ConnectionError, SystemExit])
def test_interrupted_call_keeps_reservation_and_unknown_charge_without_replay(interruption):
    records = []
    calls = []

    class Interrupted:
        supports_agent_turns = True

        def execute(self, *args):
            calls.append(1)
            raise interruption("owned-synthetic-interruption")

    ledger = BudgetLedger(
        Budget(max_usd="1", max_requests=2),
        reserve_call=lambda *a: uuid4(),
        record_call=lambda identifier, result, status: records.append(status),
    )
    with pytest.raises(interruption):
        ledger.call(Interrupted(), model(), "")
    assert calls == [1] and ledger.requests == 1 and ledger.reserved > 0
    assert not ledger.billing_complete and ledger.known_cost_requests == 0
    assert records == (["uncertain"] if interruption is ConnectionError else [])


def test_lost_receipt_acknowledgement_does_not_repeat_the_observed_request():
    calls = []

    class Native:
        supports_agent_turns = True

        def execute(self, *args):
            calls.append(1)
            return Execution("owned", Decimal("0.001"), 1)

    def uncertain(*args):
        raise ConnectionError("owned-private-write-details")

    ledger = BudgetLedger(
        Budget(max_usd="1", max_requests=2), reserve_call=lambda *a: uuid4(), record_call=uncertain
    )
    with pytest.raises(ValueError, match="WRITE_UNCERTAIN_NO_REQUEST_RETRY") as error:
        ledger.call(Native(), model(), "")
    assert "private" not in str(error.value) and calls == [1]
    assert ledger.spent == Decimal("0.001") and not ledger.billing_complete
    with pytest.raises(ValueError, match="ACK_UNCERTAIN_NO_FURTHER_REQUESTS"):
        ledger.call(Native(), model(), "")
    assert calls == [1]


def test_single_call_models_keep_the_released_serialized_shape():
    baseline = ModelSpec(key="owned-single", provider="openai", model="gpt-4.1-mini")
    assert "agent_turn" not in baseline.model_dump(mode="json")
    with pytest.raises(ValueError, match="SEPARATE_QUALIFIED"):
        model(instruction="must not bypass the model-visible frame")
