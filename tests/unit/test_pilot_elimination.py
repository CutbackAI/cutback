from decimal import Decimal

from support_evaluation import dataset_body, plan_for

from cutback.evals.datasets import prepare_dataset
from cutback.evals.experiments import Execution, pilot_items, plan_summary, run


class PilotExecutor:
    def __init__(self, bad=False, outage=False):
        self.calls = []
        self.bad, self.outage = bad, outage

    def execute(self, model, text, timeout_seconds):
        self.calls.append((model.key, text))
        if self.outage:
            raise TimeoutError("synthetic outage")
        return Execution("B" if self.bad else "A", Decimal("0.0001"), 1)


def test_pilot_uses_disjoint_validation_groups_and_all_calls_fit_frozen_budget():
    dataset = prepare_dataset(dataset_body())
    spec = plan_for(dataset).model_dump(mode="json")
    spec.update(pilot_samples=3, pilot_minimum=0.8)
    plan = type(plan_for(dataset)).model_validate(spec)
    summary = plan_summary(plan, dataset)
    pilot = pilot_items(plan, dataset)
    assert len(pilot) == 3
    heldout = {item["group_hash"] for item in dataset["items"] if item["split"] == "test"}
    assert not heldout & {item["group_hash"] for item in pilot}
    native = PilotExecutor()
    result = run(plan, dataset, native, approval_hash=summary["plan_hash"], independent_labels=True)
    assert result["status"] == "COMPLETED" and result["requests"] == summary["requests"]
    assert result["validation_pilots"][0]["promoted"]
    assert not result["validation_pilots"][0]["heldout_quality_claim"]
    assert result["models"][1]["requests"] == summary["sample_n"]
    assert Decimal(result["known_spend_subtotal_usd"]) == Decimal("0.0001") * result["requests"]


def test_failed_pilot_eliminates_candidate_and_skips_unneeded_heldout_baseline():
    dataset = prepare_dataset(dataset_body())
    plan = plan_for(dataset).model_copy(update={"pilot_samples": 3, "pilot_minimum": 0.8})
    summary = plan_summary(plan, dataset)
    for outage in (False, True):
        native = PilotExecutor(bad=True, outage=outage)
        result = run(
            plan, dataset, native, approval_hash=summary["plan_hash"], independent_labels=True
        )
        assert result["status"] == "COMPLETED"
        assert len(native.calls) == 3 and result["requests"] == 3
        assert result["eliminated_candidates"] == [plan.candidates[0].key]
        assert not result["models"] and not result["recommendations"]
        assert result["billing_complete"] is (not outage)
        if outage:
            assert result["spent_modeled_usd"] is None
        assert not result["production_deployed"]
