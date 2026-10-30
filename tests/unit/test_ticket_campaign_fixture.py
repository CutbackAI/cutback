"""The independent task has three answers and keeps related variants in one family."""

from collections import Counter
from uuid import uuid4

from cutback.evals.datasets import DatasetImport, prepare_dataset
from cutback.evals.experiments import ModelSpec
from cutback.sample_app.ticket_fixture import INSTRUCTION, cases, expected


def test_ticket_task_has_multiple_labels_and_preserves_sixty_declared_intent_families():
    rows = cases()
    assert Counter(row["gold"] for row in rows) == {"billing": 80, "access": 80, "technical": 80}
    assert len({row["input"] for row in rows}) == len(rows) == 240
    assert all(expected(row["input"]) == row["gold"] for row in rows)
    body = DatasetImport(
        workflow="tickets",
        policy_id=str(uuid4()),
        source_type="synthetic_fixture",
        independent_labels=True,
        provenance_ref="owned-ticket-task",
        cases=rows,
    )
    frozen = prepare_dataset(body)
    assert frozen["group_count"] == 60
    assert frozen["split_counts"] == {"train": 144, "validation": 48, "test": 48}
    for group in {row["group"] for row in rows}:
        members = [item for item in frozen["items"] if item["case"]["group"] == group]
        assert len(members) == 4 and len({item["split"] for item in members}) == 1


def test_owned_native_fixture_rejects_a_constant_cheap_answer_for_the_three_label_task():
    from cutback.demo_campaigns import synthetic_executor

    executor, requests = synthetic_executor(ticket_router=True)
    baseline = ModelSpec(key="baseline", provider="openai", model="gpt-4.1", max_output_tokens=256)
    unsafe = baseline.model_copy(update={"key": "unsafe", "model": "gpt-4.1-mini"})
    generated = unsafe.model_copy(
        update={"key": "generated", "instruction": INSTRUCTION, "max_output_tokens": 64}
    )
    try:
        for case in cases()[:3]:
            assert executor.execute(baseline, case["input"], 5).output == case["gold"]
            assert executor.execute(unsafe, case["input"], 5).output == "billing"
            assert executor.execute(generated, case["input"], 5).output == case["gold"]
        assert len(requests) == 9
    finally:
        executor.close()
