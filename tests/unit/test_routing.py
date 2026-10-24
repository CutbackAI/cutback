from decimal import Decimal

import pytest
from pydantic import ValidationError
from support_evaluation import SyntheticExecutor, dataset_body, plan_for

from cutback.evals.datasets import prepare_dataset
from cutback.evals.executors import ConfiguredExecutor
from cutback.evals.experiments import (
    BudgetLedger,
    Execution,
    ModelSpec,
    plan_summary,
    provider_models,
    route_target,
    run,
    upper_cost,
)


def router():
    return ModelSpec.model_validate(
        {
            "key": "bounded-route",
            "provider": "router",
            "model": "short-input-router-v1",
            "routes": [
                {
                    "max_input_bytes": 30,
                    "target": {
                        "key": "small",
                        "provider": "local",
                        "model": "synthetic-small",
                        "local_cost_upper_usd": "0.0001",
                    },
                },
                {
                    "max_input_bytes": 100,
                    "target": {
                        "key": "large",
                        "provider": "local",
                        "model": "synthetic-large",
                        "local_cost_upper_usd": "0.001",
                    },
                },
            ],
        }
    )


def test_router_reserves_maximum_and_selects_exactly_one_native_target():
    model = router()
    assert upper_cost(model) == Decimal("0.001")
    assert route_target(model, "short").key == "small"
    assert route_target(model, "x" * 31).key == "large"
    with pytest.raises(ValueError, match="NO_APPROVED_ROUTE"):
        route_target(model, "x" * 101)
    client = ConfiguredExecutor()

    class Native:
        def __init__(self):
            self.calls = []

        def execute(self, target, text, timeout_seconds):
            self.calls.append(target)
            return Execution("A", target.local_cost_upper_usd, 1)

    native = Native()
    client.clients["local"] = native
    plan = plan_for({"immutable_hash": "a" * 64})
    reservations = []
    ledger = BudgetLedger(plan.budget, reserve=reservations.append)
    result = ledger.call(client, model, "short")
    assert result.actual_model == "local:synthetic-small"
    assert reservations == [Decimal("0.001")]
    assert len(native.calls) == 1 and ledger.requests == 1
    assert ledger.spent == Decimal("0.0001")
    with pytest.raises(ValueError, match="NO_APPROVED_ROUTE"):
        ledger.call(client, model, "x" * 101)
    assert len(native.calls) == 1


def test_router_freezes_native_vendor_scope_and_rejects_nested_or_oversized_routes():
    dataset = prepare_dataset(dataset_body())
    base = plan_for(dataset).model_dump(mode="json")
    candidate = router().model_dump(mode="json")
    candidate["routes"][0]["target"].update(provider="openai", model="gpt-4.1-mini")
    base["candidates"] = [candidate]
    with pytest.raises(ValidationError, match="FORBIDDEN_PROVIDER"):
        type(plan_for(dataset)).model_validate(base)
    base["approved_providers"].append("openai")
    approved = type(plan_for(dataset)).model_validate(base)
    assert len(provider_models(approved.candidates[0])) == 2
    invalid = router().model_copy(deep=True)
    invalid.routes[0].target = router()
    with pytest.raises(ValueError, match="NESTED"):
        provider_models(invalid)
    invalid = router().model_copy(deep=True)
    invalid.routes[0].target.max_input_tokens = 4096
    with pytest.raises(ValueError, match="TOKEN_BOUND"):
        provider_models(invalid)
    changed = approved.model_copy(deep=True)
    changed.candidates[0].routes[0].max_input_bytes = 29
    assert (
        plan_summary(approved, dataset)["plan_hash"] != plan_summary(changed, dataset)["plan_hash"]
    )


def test_routed_candidate_receives_paired_heldout_evaluation():
    dataset = prepare_dataset(dataset_body())
    plan = plan_for(dataset).model_copy(update={"candidates": [router()]})
    client = ConfiguredExecutor()
    client.clients["local"] = SyntheticExecutor()
    result = run(
        plan,
        dataset,
        client,
        approval_hash=plan_summary(plan, dataset)["plan_hash"],
        independent_labels=True,
    )
    assert result["status"] == "COMPLETED"
    candidate = result["models"][1]
    assert sum(candidate["routed_models"].values()) == candidate["requests"]
    assert candidate["application_hosting_cost_usd"] is None
