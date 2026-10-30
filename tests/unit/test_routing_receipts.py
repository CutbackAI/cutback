"""A failed next call cannot borrow an earlier successful routing receipt."""

from decimal import Decimal
from uuid import uuid4

import pytest

from cutback.evals.experiments import Budget, BudgetLedger, Execution, ModelSpec


@pytest.mark.parametrize("failure", ["reservation", "native", "unknown_usage", "receipt"])
def test_next_call_clears_previous_receipt_and_preserves_unknown_reservations(failure):
    identities = [uuid4(), uuid4()]
    dispatched = []
    reserved = []
    recorded = []

    def reserve(*args):
        identity = identities[len(reserved)]
        reserved.append(identity)
        if len(reserved) == 2 and failure == "reservation":
            raise ConnectionError("owned-lost-reservation-ack")
        return identity

    def record(identity, result, status):
        recorded.append((identity, status))
        if identity == identities[1] and failure == "receipt":
            raise ConnectionError("owned-lost-receipt-ack")

    class Native:
        def execute(self, *args):
            dispatched.append(1)
            if len(dispatched) == 2:
                if failure == "native":
                    raise ConnectionError("owned-native-outage")
                if failure == "unknown_usage":
                    return Execution("owned", None, 1)
            return Execution("owned", Decimal("0.000001"), 1)

    ledger = BudgetLedger(
        Budget(max_usd="1", max_requests=3), reserve_call=reserve, record_call=record
    )
    model = ModelSpec(key="owned", provider="openai", model="gpt-4.1-mini")
    ledger.call(Native(), model, "owned-short-input", traffic_kind="optimization")
    assert ledger.last_call_id == identities[0] and ledger.billing_complete
    prior_reservation = ledger.reserved
    with pytest.raises((ValueError, ConnectionError)):
        ledger.call(Native(), model, "owned-next-input", traffic_kind="optimization")
    assert ledger.last_call_id is None
    assert not ledger.billing_complete and ledger.reserved >= prior_reservation
    assert len(dispatched) == (1 if failure == "reservation" else 2)
    if failure != "reservation":
        assert ledger.reserved > prior_reservation and recorded[-1][0] == identities[1]
    if failure in {"reservation", "receipt"}:
        with pytest.raises(ValueError, match="NO_FURTHER_REQUESTS"):
            ledger.call(Native(), model, "must-not-replay")
        assert len(dispatched) == (1 if failure == "reservation" else 2)
