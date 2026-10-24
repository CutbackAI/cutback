import hashlib
import json

import pytest
from support_evaluation import SyntheticExecutor, dataset_body, plan_for

from cutback.evals.datasets import digest, prepare_dataset
from cutback.evals.experiments import Execution, plan_summary, run
from cutback.evals.metrics import MetricSpec, evaluate, grouped_confidence
from cutback.pricing import Catalog
from cutback.server.plan_artifacts import public_config


def test_changed_catalog_invalidates_approval_before_execution(tmp_path, monkeypatch):
    dataset = prepare_dataset(dataset_body())
    plan = plan_for(dataset)
    data = Catalog().data
    data["rates"][0]["input"] = "9.99"
    raw = json.dumps(data).encode()
    path = tmp_path / "new-catalog.json"
    path.write_bytes(raw)
    monkeypatch.setenv("CUTBACK_PRICE_CATALOG", str(path))
    monkeypatch.setenv("CUTBACK_PRICE_SHA256", hashlib.sha256(raw).hexdigest())
    executor = SyntheticExecutor()
    with pytest.raises(ValueError, match="PRICE_CONTRACT_CHANGED"):
        run(plan, dataset, executor, approval_hash=digest(plan.model_dump(mode="json")))
    assert not executor.calls


def test_judges_repeat_under_shared_reserved_budget():
    dataset = prepare_dataset(dataset_body(40))
    plan = plan_for(
        dataset,
        repeats=2,
        judge={
            "key": "judge",
            "provider": "local",
            "model": "synthetic",
            "local_cost_upper_usd": "0.001",
        },
        judge_rubric="Synthetic comparison; no semantic verification.",
    )

    class Judge(SyntheticExecutor):
        def execute(self, model, text, timeout_seconds):
            result = super().execute(model, text, timeout_seconds)
            return (
                Execution('{"winner":"tie"}', result.cost_usd, 1)
                if model.key == "judge"
                else result
            )

    executor = Judge()
    result = run(plan, dataset, executor, approval_hash=digest(plan.model_dump(mode="json")))
    assert result["status"] == "COMPLETED"
    assert result["requests"] == plan_summary(plan, dataset)["requests"] == len(executor.calls)
    assert {vote["repeat"] for vote in result["judges"][0]["votes"]} == {0, 1}
    assert result["judges"][0]["disagreement_cases"] == 0


@pytest.mark.parametrize(
    "kind,case,output,expected,tier",
    [
        ("retrieval_recall", {"gold": ["a", "b"]}, '["a","c"]', 0.5, 4),
        ("ndcg", {"gold": {"a": 3, "b": 1}}, '["a","b"]', 1.0, 4),
        ("rank_agreement", {"gold": ["a", "b", "c"]}, '["c","b","a"]', 0.0, 4),
        ("numeric_match", {"gold": {"total": 12, "tax": 2}}, '{"total":12,"tax":3}', 0.5, 4),
        ("span_support", {"input": "one factual quote"}, '["factual","invented"]', 0.5, 1),
    ],
)
def test_extended_metrics_known_answers(kind, case, output, expected, tier):
    result = evaluate([case], [output], MetricSpec(kind=kind, minimum=0), independent_labels=True)
    assert result["value"] == pytest.approx(expected)
    assert result["evidence_tier"] == tier
    assert (
        evaluate([case], ["invalid"], MetricSpec(kind=kind, minimum=0), independent_labels=True)[
            "failures"
        ]
        == 1
    )


def test_perfect_micro_f1_does_not_invent_certain_population_interval():
    cases = [{"gold": {"field": "value"}} for _ in range(6)]
    answers = ['{"field":"value"}'] * 6
    metric = MetricSpec(kind="field_f1", minimum=0.9)
    evidence = grouped_confidence(
        cases,
        answers,
        list("abcdef"),
        metric,
        evaluate(cases, answers, metric, independent_labels=True),
    )
    assert evidence["confidence_95"]["low"] == 0
    assert evidence["confidence_95"]["high"] == 1


def test_sensitive_schema_and_labels_stay_in_encrypted_plan():
    value = public_config(
        {"schema_definition": {"description": "PRIVATE"}, "allowed_labels": ["PRIVATE"]}
    )
    assert "PRIVATE" not in json.dumps(value)
    assert value["schema_definition_sha256"]
