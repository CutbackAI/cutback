import json
import os
from decimal import Decimal
from uuid import uuid4

import pytest
from cryptography.exceptions import InvalidTag
from support_evaluation import SyntheticExecutor, dataset_body, plan_for

from cutback.evals.artifacts import EncryptedFileStore
from cutback.evals.datasets import digest, prepare_dataset, redact
from cutback.evals.experiments import plan_summary, run
from cutback.evals.metrics import MetricSpec, evaluate, wilson


def test_encrypted_artifacts_scoped_tamper_resistant_and_deletable(tmp_path):
    store = EncryptedFileStore(tmp_path, os.urandom(32))
    project = uuid4()
    ref = store.put(project, b"PRIVATE input", 1)
    assert b"PRIVATE" not in (tmp_path / str(project) / ref).read_bytes()
    assert store.get(project, ref) == b"PRIVATE input"
    with pytest.raises((FileNotFoundError, ValueError)):
        store.get(uuid4(), ref)
    envelope = json.loads((tmp_path / str(project) / ref).read_bytes())
    envelope["ciphertext"] = envelope["ciphertext"][:-4] + "AAAA"
    (tmp_path / str(project) / ref).write_text(json.dumps(envelope))
    with pytest.raises(InvalidTag):
        store.get(project, ref)
    store.delete(project, ref)
    assert not (tmp_path / str(project) / ref).exists()
    with pytest.raises(ValueError):
        store.get(project, "../../escape")


def test_duplicate_groups_union_and_immutable_splits():
    body = dataset_body()
    body.cases[1].input = body.cases[0].input.upper() + "  "
    body.cases[1].group = "user-b"
    body.cases[2].group = "user-b"
    first = prepare_dataset(body)
    assert first == prepare_dataset(body)
    assert len({item["split"] for item in first["items"][:3]}) == 1
    assert len({item["group_hash"] for item in first["items"][:3]}) == 1
    assert {"train", "validation", "test"} == set(first["split_counts"])
    body.cases[-1].gold = "B"
    assert first["immutable_hash"] != prepare_dataset(body)["immutable_hash"]


def test_metrics_gold_reference_schema_and_failure_denominators():
    exact = evaluate(
        [{"gold": "A"}, {"gold": "B"}],
        ["A", "A"],
        MetricSpec(kind="exact_label", minimum=0.5),
        independent_labels=True,
    )
    assert exact["value"] == 0.5 and exact["evidence_tier"] == 4
    field = evaluate(
        [{"gold": ["x"]}, {"gold": ["y"]}],
        ['["x"]', "invalid"],
        MetricSpec(kind="field_f1", minimum=0.5),
        independent_labels=True,
    )
    assert field["value"] == pytest.approx(2 / 3)
    assert field["failures"] == 1
    assert field["field_counts"] == {"tp": 1, "fp": 0, "fn": 1}
    reference = evaluate(
        [{"reference": "A"}],
        ["A"],
        MetricSpec(kind="reference_match", minimum=1),
        independent_labels=False,
    )
    assert reference["evidence_tier"] == 2 and reference["semantic_quality"] == "NOT VERIFIED"
    schema = MetricSpec(kind="json_schema", minimum=1, schema_definition={"type": "object"})
    assert evaluate([{}, {}], ["{}", "broken"], schema, independent_labels=False)["value"] == 0.5
    with pytest.raises(ValueError):
        MetricSpec(kind="json_schema", minimum=1, schema_definition={"$ref": "https://secret"})
    assert wilson(0, 0) == (None, None)


def test_task_inference_does_not_promote_references_or_prose_to_gold():
    from cutback.evals.datasets import infer_task

    body = dataset_body()
    assert infer_task(body)["suggested_metric"] == "exact_label"
    for case in body.cases:
        case.gold = {"field": "synthetic"}
    assert infer_task(body)["suggested_metric"] == "field_f1"
    body.independent_labels = False
    for case in body.cases:
        case.reference = "synthetic reference"
    assert infer_task(body)["suggested_metric"] == "reference_match"
    assert infer_task(body)["semantic_quality"] == "NOT VERIFIED"
    body.independent_labels = True
    for i, case in enumerate(body.cases):
        case.gold = f"Distinct long prose {i}" + "x" * 200
    assert infer_task(body)["kind"] == "unknown"


def test_plan_approval_budget_and_paired_quality():
    dataset = prepare_dataset(dataset_body())
    plan = plan_for(dataset)
    executor = SyntheticExecutor()
    with pytest.raises(ValueError, match="NO_API_BUDGET"):
        run(plan, dataset, executor, approval_hash=None)
    assert executor.calls == []
    result = run(
        plan,
        dataset,
        executor,
        approval_hash=digest(plan.model_dump(mode="json")),
        independent_labels=True,
        representative=False,
    )
    assert result["status"] == "COMPLETED"
    assert result["requests"] == plan_summary(plan, dataset)["requests"]
    assert result["recommendations"][0]["status"] == "PROMISING"
    assert result["recommendations"][0]["monthly_savings_usd"] is None
    paired = [text for model, text in executor.calls if model == "baseline"]
    assert paired == [text for model, text in executor.calls if model == "candidate"]
    failed = run(
        plan,
        dataset,
        SyntheticExecutor(wrong=True),
        approval_hash=digest(plan.model_dump(mode="json")),
        independent_labels=True,
    )
    assert not failed["recommendations"][0]["quality_gate_passed"]
    assert failed["recommendations"][0]["status"] == "NEEDS_VALIDATION"


def test_budget_refusal_happens_before_any_call_and_failures_keep_reservation():
    dataset = prepare_dataset(dataset_body())
    plan = plan_for(dataset, budget={"max_usd": "0", "max_requests": 1})
    executor = SyntheticExecutor()
    with pytest.raises(ValueError, match="BUDGET_TOO_SMALL"):
        run(plan, dataset, executor, approval_hash=digest(plan.model_dump(mode="json")))
    assert not executor.calls
    plan = plan_for(dataset)
    result = run(
        plan,
        dataset,
        SyntheticExecutor(fail=True),
        approval_hash=digest(plan.model_dump(mode="json")),
    )
    assert Decimal(result["reserved_usd"]) > 0
    assert "PRIVATE" not in json.dumps(result)
    assert not result["recommendations"][0]["quality_gate_passed"]


def test_redaction_denies_fields_and_common_secrets():
    assert redact(
        {"password": "secret", "input": "a@example.org sk-" + "a" * 32}, ["password"]
    ) == {"input": "[EMAIL] [SECRET]"}


def test_judge_budget_is_shared_and_unlabeled_results_never_verified():
    body = dataset_body()
    for case in body.cases:
        case.gold = None
    dataset = prepare_dataset(body)
    plan = plan_for(
        dataset,
        metric={"kind": "nonempty", "minimum": 1, "min_test_samples": 3},
        judge={
            "key": "judge",
            "provider": "local",
            "model": "synthetic-judge",
            "local_cost_upper_usd": "0.001",
        },
        judge_rubric="Compare clarity only; semantic correctness is not independently known.",
    )

    class JudgeExecutor(SyntheticExecutor):
        def execute(self, model, text, timeout_seconds):
            result = super().execute(model, text, timeout_seconds)
            if model.key == "judge":
                from cutback.evals.experiments import Execution

                return Execution('{"winner":"B"}', result.cost_usd, result.latency_ms)
            return result

    executor = JudgeExecutor()
    result = run(
        plan,
        dataset,
        executor,
        approval_hash=digest(plan.model_dump(mode="json")),
        independent_labels=False,
        representative=True,
    )
    assert result["status"] == "COMPLETED"
    assert len(executor.calls) == plan_summary(plan, dataset)["requests"]
    assert result["judges"][0]["semantic_quality"] == "NOT VERIFIED"
    assert result["recommendations"][0]["status"] == "PROMISING"
    assert result["recommendations"][0]["evidence_tier"] == 1
