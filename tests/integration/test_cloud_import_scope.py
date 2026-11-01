"""Cloud assertions remain external, encrypted and scoped through preview and commit."""

import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from integration.test_import_api import import_body as import_body
from sqlalchemy import select

from cutback.server.db import CapturePolicyRecord, DatasetRecord

ROOT = Path(__file__).resolve().parents[2] / "tests/fixtures/imports"
CASES = [
    ("bedrock-automated-model-jsonl.v1", "bedrock-automated-model.jsonl"),
    ("azure-ai-evaluation-1.18.10-results.v1", "azure-ai-evaluation-1.18.10-results.json"),
    ("vertexai-1.153.1-metrics-jsonl.v1", "vertexai-1.153.1-metrics.jsonl"),
]
pytestmark = pytest.mark.postgres


def body_for(import_body, profile, filename):
    raw = (ROOT / filename).read_text()
    marker = "SYNTHETIC_CLOUD_METRIC_NAME_NOT_METADATA"
    if profile.startswith("bedrock"):
        row = json.loads(raw)
        row["automatedEvaluationResult"]["scores"][0]["metricName"] = marker
        raw = json.dumps(row)
    elif profile.startswith("azure"):
        value = json.loads(raw)
        for row in value["rows"]:
            row["outputs." + marker + ".score"] = row.pop("outputs.owned_accuracy.score")
        raw = json.dumps(value)
    else:
        rows = [json.loads(line) for line in raw.splitlines()]
        for row in rows:
            row[marker + "/score"] = row.pop("exact_match/score")
        raw = "\n".join(json.dumps(row) for row in rows)
    return {
        **import_body,
        "profile": profile,
        "source_text": raw,
        "source_sha256": hashlib.sha256(raw.encode()).hexdigest(),
    }, marker


@pytest.mark.parametrize("profile,filename", CASES)
def test_cloud_metrics_require_current_purpose_reads_and_never_enter_plaintext_provenance(
    api, tenant, import_body, profile, filename
):
    body, marker = body_for(import_body, profile, filename)
    client, admin, app = api
    base = f"/v1/projects/{tenant[0]}"
    response = client.post(base + "/imports/preview", headers=admin, json=body)
    assert response.status_code == 201, response.text
    preview = response.json()
    assert marker not in response.text and preview["independent_labels"] is False
    assert preview["provider_calls"] == 0 and preview["source_asserted_checks"] > 0
    path = base + "/imports/" + preview["id"]
    assert (
        client.post(
            path + "/content", headers=tenant[2], json={"purpose": "owned-read"}
        ).status_code
        == 403
    )
    content = client.post(
        path + "/content", headers=admin, json={"purpose": "owned-source-mapping"}
    )
    assert content.status_code == 200 and marker in content.text
    assert all(
        row["evidence"] == "externally_asserted" for row in content.json()["source_asserted_checks"]
    )
    other = client.post("/v1/projects", headers=admin, json={"slug": "other-" + uuid4().hex})
    assert other.status_code == 201
    assert client.get(path.replace(tenant[0], other.json()["id"]), headers=admin).status_code == 404
    commit = {
        "preview_hash": preview["preview_hash"],
        "mapping_reviewed": True,
        "rights_confirmed": True,
    }
    created = client.post(path + "/commit", headers=admin, json=commit)
    assert created.status_code == 201, created.text
    metadata = client.get(base + "/datasets/" + created.json()["id"], headers=tenant[2])
    assert metadata.status_code == 200 and marker not in metadata.text
    assert metadata.json()["provenance"]["independent_labels"] is False
    assert "metric_name" not in json.dumps(metadata.json()["provenance"]["source_asserted_checks"])
    with app.state.sessions() as session:
        dataset = session.scalar(
            select(DatasetRecord).where(DatasetRecord.id == UUID(created.json()["id"]))
        )
        assert marker not in json.dumps(dataset.provenance)
    with app.state.sessions.begin() as session:
        session.get(CapturePolicyRecord, UUID(body["policy_id"])).enabled = False
    assert (
        client.post(path + "/content", headers=admin, json={"purpose": "owned-revoked"}).status_code
        == 403
    )
    assert client.post(path + "/commit", headers=admin, json=commit).status_code == 403


def test_large_cloud_metric_collections_keep_bounded_metadata_and_explicit_scope(
    api, tenant, import_body
):
    row = {
        "prompt": "Owned small input",
        "response": "Historical",
        **{f"metric-{i}/score": i for i in range(64)},
    }
    raw = json.dumps(row)
    body = {
        **import_body,
        "profile": "vertexai-1.153.1-metrics-jsonl.v1",
        "source_text": raw,
        "source_sha256": hashlib.sha256(raw.encode()).hexdigest(),
    }
    base = f"/v1/projects/{tenant[0]}"
    response = api[0].post(base + "/imports/preview", headers=api[1], json=body)
    assert response.status_code == 201, response.text
    preview = response.json()
    path = base + "/imports/" + preview["id"]
    content = api[0].post(
        path + "/content", headers=api[1], json={"purpose": "owned-bounded-metrics"}
    )
    assert content.status_code == 200 and content.json()["checks_truncated"] is True
    assert len(content.json()["source_asserted_checks"]) == 25
    created = api[0].post(
        path + "/commit",
        headers=api[1],
        json={
            "preview_hash": preview["preview_hash"],
            "mapping_reviewed": True,
            "rights_confirmed": True,
        },
    )
    assert created.status_code == 201, created.text
    metadata = (
        api[0]
        .get(base + "/datasets/" + created.json()["id"], headers=tenant[2])
        .json()["provenance"]
    )
    assert len(metadata["source_asserted_checks"]) == 25
    assert metadata["source_asserted_checks_total"] == 64
    assert metadata["source_asserted_checks_truncated"] is True
    assert "not_task_or_population_coverage" in metadata["source_asserted_checks_scope"]


@pytest.mark.parametrize("number,status", [("1e1000000", 201), ("1e999999999999999999999999", 422)])
def test_extreme_numeric_exports_fail_closed_without_server_errors_or_source_echo(
    api, tenant, import_body, number, status
):
    raw = (
        '{"prompt":"SYNTHETIC_IMPORT_INPUT","response":"x","metric/score":' + number + "}\n"
        '{"prompt":"Owned valid row","response":"x","metric/score":1}\n'
    )
    body = {
        **import_body,
        "profile": "vertexai-1.153.1-metrics-jsonl.v1",
        "source_text": raw,
        "source_sha256": hashlib.sha256(raw.encode()).hexdigest(),
    }
    result = api[0].post(f"/v1/projects/{tenant[0]}/imports/preview", headers=api[1], json=body)
    assert result.status_code == status, result.text
    assert "SYNTHETIC_IMPORT_INPUT" not in result.text and number not in result.text
    if status == 201:
        assert result.json()["accepted"] == result.json()["rejected"] == 1
    else:
        assert result.json()["code"] == "MALFORMED_OR_DUPLICATE_IMPORT_JSON"
