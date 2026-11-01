"""Automatic choices cannot turn ambiguity, unknown costs or truncation into approval."""

import hashlib
import json
from copy import deepcopy
from uuid import uuid4

import pytest
from support_policy_application import application

from cutback.agent_policy_data import discover, verify_baseline
from cutback.agent_policy_program import prepare


class Metadata:
    endpoint = "https://collector.example.invalid"

    def __init__(self):
        self.project, self.env, self.dataset, self.snapshot = [str(uuid4()) for _ in range(4)]
        self.calls = []
        self.datasets = [
            {
                "id": self.dataset,
                "hash": "a" * 64,
                "workflow": "routing-task",
                "provenance": {
                    "environment": "staging",
                    "independent_labels": True,
                    "source_type": "synthetic_fixture",
                    "split_counts": {"validation": 40, "test": 40},
                },
            }
        ]
        summary = {
            "window_cost_complete_under_declaration": True,
            "task_count_matches_declaration": True,
            "declared_successes": 1,
            "declared_window_cost_per_success_usd": "1.000000000001",
        }
        self.report = {
            "environment_id": self.env,
            "summary": summary,
            "items": [
                {"declaration": {"workflow": "routing-task", "service": "owned", "release": "v1"}}
            ],
        }
        self.pages = [
            {
                "items": [
                    {
                        "id": self.snapshot,
                        "workflow": "routing-task",
                        "environment_id": self.env,
                        "summary": deepcopy(summary),
                        "evidence_sha256": self.digest(),
                    }
                ],
                "next_after": None,
            }
        ]

    def digest(self):
        return hashlib.sha256(
            json.dumps(self.report, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def __call__(self, method, path, body=None):
        self.calls.append((method, path))
        assert method == "GET", "DISCOVERY_MUST_NOT_MUTATE_OR_APPROVE"
        if path.endswith("/ready"):
            return {"source_policy_program_contract": "cutback.source-policy-program.v1"}
        if "/environments?" in path:
            return {"items": [{"id": self.env, "name": "staging"}], "next_cursor": None}
        if "/datasets?" in path:
            return self.datasets
        if path.endswith("/datasets/" + self.dataset):
            return {**self.datasets[0], "split_counts": {"train": 20}}
        if "/task-cost-snapshots?" in path:
            index = sum("/task-cost-snapshots?" in p for _, p in self.calls) - 1
            return self.pages[index]
        assert path.endswith("/task-cost-snapshots/" + self.snapshot)
        return {
            "id": self.snapshot,
            "environment_id": self.env,
            "evidence_sha256": self.digest(),
            "report": self.report,
        }


def lookup(tmp_path, remote):
    root = application(tmp_path)
    return root, discover(root, remote, remote.project, producer_file="app.py", fixture=True)


def test_unique_scoped_metadata_is_not_source_quality_or_execution_authority(tmp_path):
    remote = Metadata()
    _, result = lookup(tmp_path, remote)
    assert result["selected_dataset_id"] == remote.dataset
    assert result["selected_baseline_snapshot_id"] == remote.snapshot
    verify_baseline(remote, remote.project, remote.snapshot, result)
    assert (
        result["source_aligned_baseline_verified"]
        is result["independent_quality_verified"]
        is False
    )
    assert result["paid_calls_authorized"] is result["source_change_authorized"] is False
    assert result["provider_calls"] == result["encrypted_storage_reads"] == 0
    assert result["population_coverage"] == "unknown" and result["production_savings_usd"] is None
    assert "OWNED_PRIVATE_POLICY_INSTRUCTION" not in json.dumps(result)


@pytest.mark.parametrize(
    "field,value",
    [
        ("environment", "production"),
        ("independent_labels", False),
        ("independent_labels", "true"),
        ("source_type", "human_labeled"),
        ("split_counts", {"validation": 40, "test": 0}),
    ],
)
def test_incompatible_or_unknown_labels_never_become_automatic_data(tmp_path, field, value):
    remote = Metadata()
    remote.datasets[0]["provenance"][field] = value
    _, result = lookup(tmp_path, remote)
    assert result["selected_dataset_id"] is None


@pytest.mark.parametrize("value", [None, "NaN", "Infinity", "-0.1", 1.25, True])
def test_unknown_or_invalid_costs_never_become_an_automatic_baseline(tmp_path, value):
    remote = Metadata()
    remote.pages[0]["items"][0]["summary"]["declared_window_cost_per_success_usd"] = value
    _, result = lookup(tmp_path, remote)
    assert result["selected_baseline_snapshot_id"] is None


def test_no_implicit_newest_selection_or_approval_for_ambiguous_datasets(tmp_path):
    remote = Metadata()
    remote.datasets.append({**deepcopy(remote.datasets[0]), "id": str(uuid4())})
    root, result = lookup(tmp_path, remote)
    assert result["dataset_candidates"] == 2 and result["selected_dataset_id"] is None
    remote.calls.clear()
    with pytest.raises(ValueError, match="SELECT_EXPLICIT_DATASET"):
        prepare(
            root,
            remote,
            remote.project,
            producer_file="app.py",
            purpose="owned-discovery",
            max_usd="1",
        )
    assert not (root / ".cutback/agent/policy-program.json").exists()
    assert all(method == "GET" for method, _ in remote.calls)


def test_full_dataset_page_and_remaining_baseline_cursor_preserve_unknown_choices(tmp_path):
    remote = Metadata()
    remote.datasets += [
        {**deepcopy(remote.datasets[0]), "id": str(uuid4()), "workflow": "other"} for _ in range(99)
    ]
    template = deepcopy(remote.pages[0]["items"][0])
    remote.pages = []
    for _ in range(5):
        items = [{**deepcopy(template), "id": str(uuid4()), "workflow": "other"} for _ in range(20)]
        remote.pages.append({"items": items, "next_after": items[-1]["id"]})
    remote.pages[0]["items"][0] = template
    _, result = lookup(tmp_path, remote)
    assert not result["dataset_listing_complete"] and not result["baseline_listing_complete"]
    assert result["selected_dataset_id"] is result["selected_baseline_snapshot_id"] is None
    assert len(remote.calls) == 7


@pytest.mark.parametrize("change", ["scope", "evidence", "mixed_release", "missing_release"])
def test_chosen_baseline_is_refreshed_and_actual_identities_are_required(tmp_path, change):
    remote = Metadata()
    _, result = lookup(tmp_path, remote)
    if change == "scope":
        remote.env = str(uuid4())
    elif change == "evidence":
        remote.report["summary"]["declared_successes"] = 2
    elif change == "mixed_release":
        remote.report["items"].append(
            {"declaration": {"workflow": "routing-task", "service": "owned", "release": "v2"}}
        )
        result["baseline_snapshots"][0]["evidence_sha256"] = remote.digest()
    else:
        remote.report["items"][0]["declaration"]["release"] = None
        result["baseline_snapshots"][0]["evidence_sha256"] = remote.digest()
    with pytest.raises(ValueError, match="POLICY_DATA_"):
        verify_baseline(remote, remote.project, remote.snapshot, result)


@pytest.mark.parametrize("case", ["cursor", "scope", "duplicate"])
def test_duplicate_scope_page_and_invalid_cursor_stop_without_mutation(tmp_path, case):
    remote = Metadata()
    if case == "cursor":
        remote.pages[0]["next_after"] = str(uuid4())
    elif case == "scope":
        remote.pages[0]["items"][0]["environment_id"] = str(uuid4())
    else:
        remote.pages[0]["items"].append(deepcopy(remote.pages[0]["items"][0]))
    with pytest.raises(ValueError, match="POLICY_DATA_"):
        lookup(tmp_path, remote)


def test_prepare_freezes_discovered_ids_and_resume_does_not_choose_later_versions(tmp_path):
    class StopAtUpload(Exception):
        pass

    class Remote(Metadata):
        def __call__(self, method, path, body=None):
            if method == "POST":
                assert path.endswith("/source-policies")
                raise StopAtUpload
            return super().__call__(method, path, body)

    remote = Remote()
    root = application(tmp_path)
    options = {
        "producer_file": "app.py",
        "purpose": "owned-auto-data",
        "max_usd": "1",
        "generate": False,
    }
    with pytest.raises(StopAtUpload):
        prepare(root, remote, remote.project, **options)
    path = root / ".cutback/agent/policy-program.json"
    saved = json.loads(path.read_text())
    assert saved["intent"]["dataset_id"] == remote.dataset
    assert saved["intent"]["baseline_snapshot_id"] == remote.snapshot
    assert saved["preparation_data_selection"]["status"] == "UNIQUE_COMPATIBLE_METADATA"
    assert "preparation_data_selection" not in saved["intent"]
    assert "program_id" not in saved and "approval_ref" not in saved
    remote.datasets.append({**deepcopy(remote.datasets[0]), "id": str(uuid4())})
    remote.calls.clear()
    with pytest.raises(StopAtUpload):
        prepare(root, remote, remote.project, **options)
    assert all(
        "/datasets?" not in route and "/task-cost-snapshots?" not in route
        for _, route in remote.calls
    )
    assert json.loads(path.read_text())["intent"] == saved["intent"]
    with pytest.raises(ValueError, match="SAVED_ENVIRONMENT_CHANGED"):
        prepare(root, remote, remote.project, **options, data_environment="production")


def test_frozen_snapshot_hash_uses_released_ascii_escaping(tmp_path):
    remote = Metadata()
    remote.report["items"][0]["declaration"]["source_version"] = "synthetic-unicode-\u00e9"
    remote.pages[0]["items"][0]["evidence_sha256"] = remote.digest()
    _, result = lookup(tmp_path, remote)
    verify_baseline(remote, remote.project, remote.snapshot, result)
