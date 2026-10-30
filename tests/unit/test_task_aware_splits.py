"""Versioned split policy keeps unique answer texts out of singleton class strata."""

import pytest
from support_evaluation import dataset_body

from cutback.evals.datasets import DatasetImport, digest, prepare_dataset


def unique_answers():
    body = dataset_body(24)
    for index, case in enumerate(body.cases):
        case.gold = str(14 + index)
    return body


def test_task_aware_policy_supports_unique_answer_texts_without_changing_legacy_imports():
    legacy = unique_answers()
    raw = legacy.model_dump(mode="json")
    # Released v1 content and assignments remain reproducible, including its rare strata.
    assert "split_policy" not in raw
    original = prepare_dataset(legacy)
    assert original["split_counts"] == {"test": 24}
    assert DatasetImport.model_validate(raw).model_dump(mode="json") == raw
    body = DatasetImport.model_validate({**raw, "split_policy": "task-aware-v2"})
    current = prepare_dataset(body)
    assert set(current["split_counts"]) == {"train", "validation", "test"}
    assert min(current["split_counts"].values()) >= 3
    assert current == prepare_dataset(body)
    assert current["split_strategy"] == "seeded_task_aware_group_union_v2"
    assert current["immutable_hash"] != original["immutable_hash"]


def test_classification_still_stratifies_labels_and_duplicate_customer_families_stay_together():
    legacy = dataset_body(80)
    for index, case in enumerate(legacy.cases):
        case.gold = "A" if index % 2 else "B"
    body = DatasetImport.model_validate(
        {**legacy.model_dump(mode="json"), "split_policy": "task-aware-v2"}
    )
    old, new = prepare_dataset(legacy), prepare_dataset(body)
    assert [i["split"] for i in old["items"]] == [i["split"] for i in new["items"]]
    body.cases[1].input = body.cases[0].input.upper() + "  "
    body.cases[1].group = body.cases[2].group = "owned-customer-family"
    new = prepare_dataset(body)
    assert len({i["group_hash"] for i in new["items"][:3]}) == 1
    assert len({i["split"] for i in new["items"][:3]}) == 1


def test_new_split_policy_is_explicit_and_changes_the_immutable_content_contract():
    body = unique_answers()
    raw = body.model_dump(mode="json")
    assert digest(raw) == prepare_dataset(body)["immutable_hash"]
    with pytest.raises(ValueError):
        DatasetImport.model_validate({**raw, "split_policy": "arbitrary-repartition"})
    assert "split_policy" not in DatasetImport.model_validate(
        {**raw, "split_policy": None}
    ).model_dump(mode="json")
