from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from support_outcome_watch import prepare_watch

pytestmark = pytest.mark.postgres


def test_draft_derives_source_hashes_and_canonical_runs_and_reconciles_repeated_requests(
    api, tenant, tmp_path, monkeypatch
):
    base, env, reference, _, _ = prepare_watch(api, tenant, tmp_path, monkeypatch, approve=False)
    intent = {
        key: value
        for key, value in reference.items()
        if key not in {"baseline_sha256", "source_diff_sha256", "optimization_run_ids"}
    }
    intent["watch_id"] = str(uuid4())
    path = base + "/outcome-watches/draft"
    with ThreadPoolExecutor(max_workers=3) as pool:
        replies = list(
            pool.map(
                lambda _: api[0].post(
                    path, headers=api[1], params={"environment_id": env}, json=intent
                ),
                range(3),
            )
        )
    assert all(reply.status_code == 201 for reply in replies), [reply.text for reply in replies]
    assert len({reply.json()["id"] for reply in replies}) == 1
    row = replies[0].json()
    assert (
        row["status"] == "PENDING" and not row["enabled"] and row["provider_calls_authorized"] == 0
    )
    policy = row["specification"]["policy"]
    assert policy["baseline_sha256"] == reference["baseline_sha256"]
    assert policy["source_diff_sha256"] == reference["source_diff_sha256"]
    assert policy["optimization_run_ids"] == reference["optimization_run_ids"]
    injected = api[0].post(
        path,
        headers=api[1],
        params={"environment_id": env},
        json={**intent, "optimization_run_ids": [str(uuid4())]},
    )
    assert injected.status_code == 422
    assert (
        api[0]
        .post(path, headers=tenant[2], params={"environment_id": env}, json=intent)
        .status_code
        == 403
    )
    summary = api[0].get(
        base + f"/task-cost-snapshots/{reference['baseline_snapshot_id']}/summary",
        headers=tenant[2],
    )
    assert summary.status_code == 200 and "items" not in summary.json()
    assert summary.json()["service"] == "synthetic-shop"
