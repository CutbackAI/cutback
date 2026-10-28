import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from test_monitoring import span_body

from cutback.evals.artifacts import EncryptedFileStore
from cutback.evals.experiments import Execution
from cutback.server.monitoring import queue_online_intake, run_online_one
from cutback.server.monitoring_db import OnlineEvaluation, OnlineRule
from cutback.server.quality_contracts import JUDGE_SCHEMA
from cutback.server.quality_db import QualityEvaluation, QualityRule
from cutback.server.quality_worker import run_quality_one

pytestmark = pytest.mark.postgres


@pytest.fixture
def quality_setup(api, tenant, tmp_path, monkeypatch):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    monkeypatch.setenv("CUTBACK_QUALITY_EXECUTION", "false")
    client, admin, app = api
    app.state.artifact_store = EncryptedFileStore(tmp_path / "owned-quality", os.urandom(32))
    base = f"/v1/projects/{tenant[0]}"
    response = client.post(
        base + "/capture-policies",
        headers=admin,
        json={
            "enabled": True,
            "approval_ref": "owned-synthetic-quality",
            "rights_basis": "synthetic",
            "environments": ["staging"],
            "workflows": ["synthetic-support"],
            "allowed_providers": ["local", "openai", "anthropic"],
            "max_samples": 200,
            "sample_rate": 1,
            "retention_days": 1,
            "expires_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
        },
    )
    assert response.status_code == 201, response.text
    yield base, response.json()["id"], app.state.artifact_store
    with app.state.sessions.begin() as session:
        for cls in (QualityRule, OnlineRule):
            session.execute(
                update(cls).where(cls.project_id == UUID(tenant[0])).values(enabled=False)
            )
        session.execute(
            update(OnlineEvaluation)
            .where(
                OnlineEvaluation.project_id == UUID(tenant[0]),
                OnlineEvaluation.state.in_(["queued", "leased"]),
            )
            .values(state="unknown", lease_token=None, lease_until=None)
        )
        session.execute(
            update(QualityEvaluation)
            .where(
                QualityEvaluation.project_id == UUID(tenant[0]),
                QualityEvaluation.state.in_(["queued", "leased"]),
            )
            .values(state="unknown", output_reserved=False, lease_token=None, lease_until=None)
        )


def create_quality(api, setup, *, kind="nonempty_output", **changes):
    client, admin, app = api
    base, policy, store = setup
    now = datetime.now(UTC)
    body = dict(
        version_id=str(uuid4()),
        key="synthetic-quality",
        expected_revision=0,
        environment="staging",
        workflow="synthetic-support",
        source_policy_id=policy,
        evaluator=kind,
        content_use_authorized=True,
        starts_at=(now - timedelta(hours=1)).isoformat(),
        expires_at=(now + timedelta(hours=6)).isoformat(),
        max_evaluations=20,
        approval_ref="owned-synthetic-quality",
    )
    if kind != "nonempty_output":
        p = client.post(
            base + "/prompts",
            headers=admin,
            json={"key": "owned-quality-" + uuid4().hex[:8], "workflow": "synthetic-support"},
        )
        assert p.status_code == 201, p.text
        schema = (
            JUDGE_SCHEMA
            if kind == "model_judge"
            else {
                "type": "object",
                "properties": {"label": {"type": "string"}},
                "required": ["label"],
                "additionalProperties": False,
            }
        )
        document = {
            "text": (
                "Judge captured input {{input}} and output {{output}}. "
                "Return an exact supporting quote."
            ),
            "variables": {"input": "string", "output": "string"},
            "output_schema": schema,
        }
        v = client.post(
            base + "/prompts/" + p.json()["id"] + "/versions",
            headers=admin,
            json={
                "version_id": str(uuid4()),
                "expected_latest_ordinal": 0,
                "policy_id": policy,
                "environment": "staging",
                "provenance_ref": "owned-quality",
                "document": document,
            },
        )
        assert v.status_code == 201, v.text
        body["prompt_version_id"] = v.json()["id"]
    if kind == "model_judge":
        body.update(
            judge={"provider": "openai", "model": "gpt-4.1-mini", "surface": "responses.create"},
            max_requests=10,
            max_total_usd="1",
        )
    body.update(changes)
    response = client.post(base + "/quality-rules", headers=admin, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def captured(api, tenant, setup, *, output="urgent", input="Owned synthetic urgent input"):
    client, admin, app = api
    base, policy, store = setup
    span = span_body()
    response = client.post("/v1/ingest/spans", headers=tenant[1], json={"spans": [span]})
    assert response.status_code == 200, response.text
    response = client.post(
        "/v1/ingest/trace-content",
        headers=tenant[1],
        json={
            "policy_id": policy,
            "sample_id": str(uuid4()),
            "trace_id": span["trace_id"],
            "span_id": span["span_id"],
            "provider": "local",
            "input": input,
            "output": output,
            "provenance_ref": "owned-synthetic-quality",
        },
    )
    assert response.status_code == 201, response.text
    queue_online_intake(app.state.sessions, enabled=True, only_project_id=UUID(tenant[0]))
    return span


def quality_rows(api, tenant, setup, rule):
    response = api[0].get(setup[0] + f"/quality-rules/{rule['id']}/evaluations", headers=tenant[2])
    assert response.status_code == 200, response.text
    assert (
        "lease_token" not in response.text and "Owned synthetic urgent input" not in response.text
    )
    return response.json()["items"]


def test_deterministic_quality_needs_content_consent_and_keeps_native_cost_zero(
    api, tenant, quality_setup
):
    rule = create_quality(api, quality_setup)
    captured(api, tenant, quality_setup, output="")
    captured(api, tenant, quality_setup, output="Owned synthetic answer")
    sessions = api[2].state.sessions
    assert run_quality_one(sessions, quality_setup[2])["status"] == "QUALITY_EXECUTION_DISABLED"
    for _ in range(2):
        run_quality_one(sessions, quality_setup[2], execution_enabled=True)
    rows = quality_rows(api, tenant, quality_setup, rule)
    assert sorted(r["state"] for r in rows) == ["failed", "passed"]
    assert all(r["known_spent_usd"] == "0E-12" or Decimal(r["known_spent_usd"]) == 0 for r in rows)
    assert all(not r["request_reserved"] for r in rows)
    for _ in range(2):
        run_online_one(sessions, enabled=True)
    base = quality_setup[0]
    observations = (
        api[0]
        .get(base + f"/online-rules/{rule['online_rule_id']}/evaluations", headers=tenant[2])
        .json()["items"]
    )
    assert sorted(r["state"] for r in observations) == ["failed", "passed"]
    assert (
        api[0]
        .post(base + "/quality-rules", headers=tenant[2], json=rule["specification"])
        .status_code
        == 403
    )


def test_model_judge_reserves_before_execute_and_encrypts_optional_output(
    api, tenant, quality_setup
):
    rule = create_quality(
        api, quality_setup, kind="model_judge", retain_judge_output=True, record_input_features=True
    )
    captured(api, tenant, quality_setup)
    sessions = api[2].state.sessions

    class Judge:
        calls = 0

        def execute(self, model, text, timeout_seconds):
            self.calls += 1
            with sessions() as session:
                record = session.get(QualityRule, UUID(rule["id"]))
                assert record.requests_used == 1 and record.reserved_usd > 0
                job = session.scalar(
                    select(QualityEvaluation).where(QualityEvaluation.rule_id == record.id)
                )
                assert job.charge_started and job.output_reserved
            assert model.request_contract.render(text)["input"]
            return Execution(
                json.dumps({"outcome": "failed", "evidence_quote": "urgent"}), Decimal("0.0001"), 1
            )

    judge = Judge()
    result = run_quality_one(sessions, quality_setup[2], execution_enabled=True, executor=judge)
    assert result["status"] == "failed", result
    rows = quality_rows(api, tenant, quality_setup, rule)
    assert judge.calls == 1 and rows[0]["output_retained"]
    assert rows[0]["result"]["evidence"] == "cutback_model_judge"
    output = api[0].post(
        quality_setup[0] + f"/quality-evaluations/{rows[0]['id']}/output",
        headers=api[1],
        json={"purpose": "owned-synthetic-investigation"},
    )
    assert output.status_code == 200, output.text
    assert json.loads(output.json()["output"])["evidence_quote"] == "urgent"
    assert (
        api[0]
        .post(
            quality_setup[0] + f"/quality-evaluations/{rows[0]['id']}/output",
            headers=tenant[2],
            json={"purpose": "not-authorized"},
        )
        .status_code
        == 403
    )


def test_crashed_judge_request_retains_uncertainty_and_never_replays(api, tenant, quality_setup):
    rule = create_quality(api, quality_setup, kind="model_judge", retain_judge_output=True)
    captured(api, tenant, quality_setup)
    sessions = api[2].state.sessions

    class Crashed:
        calls = 0

        def execute(self, *args):
            self.calls += 1
            raise SystemExit("owned synthetic worker interruption")

    judge = Crashed()
    with pytest.raises(SystemExit):
        run_quality_one(sessions, quality_setup[2], execution_enabled=True, executor=judge)
    with sessions.begin() as session:
        job = session.scalar(
            select(QualityEvaluation).where(QualityEvaluation.rule_id == UUID(rule["id"]))
        )
        job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    result = run_quality_one(sessions, quality_setup[2], execution_enabled=True, executor=judge)
    assert result["status"] == "unknown"
    rows = quality_rows(api, tenant, quality_setup, rule)
    assert judge.calls == 1 and rows[0]["known_spent_usd"] is None
    assert "BILLING_UNCERTAIN" in rows[0]["result"]["code"]


def test_concurrent_workers_cannot_exceed_one_request(api, tenant, quality_setup):
    rule = create_quality(api, quality_setup, kind="model_judge", max_requests=1)
    captured(api, tenant, quality_setup)
    captured(api, tenant, quality_setup)
    sessions = api[2].state.sessions

    class Judge:
        calls = 0

        def execute(self, *args):
            self.calls += 1
            return Execution('{"outcome":"passed","evidence_quote":"urgent"}', Decimal("0.001"), 1)

    judge = Judge()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: run_quality_one(
                    sessions, quality_setup[2], execution_enabled=True, executor=judge
                ),
                range(2),
            )
        )
    assert sorted(r["status"] for r in results) == ["passed", "unknown"]
    assert judge.calls == 1
    with sessions() as session:
        authority = session.get(QualityRule, UUID(rule["id"]))
        assert authority.requests_used == 1 and authority.spent_usd == Decimal("0.001")


@pytest.mark.parametrize("during_call", [False, True])
def test_revocation_denies_new_calls_and_post_call_evidence(
    api, tenant, quality_setup, during_call
):
    rule = create_quality(api, quality_setup, kind="model_judge", retain_judge_output=True)
    captured(api, tenant, quality_setup)
    client, admin, app = api

    def revoke():
        assert (
            client.delete(
                quality_setup[0] + "/capture-policies/" + quality_setup[1], headers=admin
            ).status_code
            == 200
        )

    class Judge:
        calls = 0

        def execute(self, *args):
            self.calls += 1
            revoke()
            return Execution('{"outcome":"passed","evidence_quote":"urgent"}', Decimal("0.001"), 1)

    if not during_call:
        revoke()
    judge = Judge()
    result = run_quality_one(
        app.state.sessions, quality_setup[2], execution_enabled=True, executor=judge
    )
    assert result["status"] == "unknown"
    assert judge.calls == int(during_call)
    row = quality_rows(api, tenant, quality_setup, rule)[0]
    assert not row["output_retained"]
    assert Decimal(row["known_spent_usd"]) == (Decimal("0.001") if during_call else 0)


def test_output_slot_is_reserved_against_shared_capture_quota(api, tenant, quality_setup):
    from cutback.server.db import CapturePolicyRecord
    from cutback.server.evaluation_api import policy_object_count

    rule = create_quality(api, quality_setup, kind="model_judge", retain_judge_output=True)
    captured(api, tenant, quality_setup)
    captured(api, tenant, quality_setup)
    sessions = api[2].state.sessions
    with sessions.begin() as session:
        policy = session.get(CapturePolicyRecord, UUID(quality_setup[1]))
        policy.policy = {**policy.policy, "max_samples": 4}

    class Judge:
        calls = 0

        def execute(self, *args):
            self.calls += 1
            with sessions() as session:
                assert policy_object_count(session, UUID(quality_setup[1])) == 4
            return Execution('{"outcome":"passed","evidence_quote":"urgent"}', Decimal("0.001"), 1)

    judge = Judge()
    for _ in range(2):
        run_quality_one(sessions, quality_setup[2], execution_enabled=True, executor=judge)
    assert judge.calls == 1
    rows = quality_rows(api, tenant, quality_setup, rule)
    assert sorted(r["state"] for r in rows) == ["passed", "unknown"]
    assert sum(r["output_retained"] for r in rows) == 1


def test_quality_export_delete_and_managed_rule_control(api, tenant, quality_setup):
    from cutback.server.quality_db import QualityPromptUse

    rule = create_quality(api, quality_setup, kind="output_schema")
    captured(api, tenant, quality_setup, output='{"label":"owned"}')
    client, admin, app = api
    assert (
        run_quality_one(app.state.sessions, quality_setup[2], execution_enabled=True)["status"]
        == "passed"
    )
    base = quality_setup[0]
    assert (
        client.post(
            base + f"/online-rules/{rule['online_rule_id']}/switch",
            headers=admin,
            json={"enabled": False, "expected_revision": 1, "expected_enabled": True},
        ).status_code
        == 409
    )
    exported = client.get(base + "/export", headers=admin).text
    assert all(
        '"' + table + '"' in exported
        for table in ("quality_rules", "quality_evaluations", "quality_prompt_uses")
    )
    assert "Owned synthetic urgent input" not in exported and "object_ref" not in exported
    slug = client.get(base, headers=admin).json()["slug"]
    assert client.delete(base, headers=admin, params={"confirm_slug": slug}).status_code == 200
    with app.state.sessions() as session:
        for cls in (QualityRule, QualityEvaluation, QualityPromptUse):
            assert session.scalar(select(cls.id).where(cls.project_id == UUID(tenant[0]))) is None


def test_expired_admission_never_starts_request(api, tenant, quality_setup):
    rule = create_quality(api, quality_setup, kind="model_judge")
    captured(api, tenant, quality_setup)
    sessions = api[2].state.sessions
    with sessions.begin() as session:
        job = session.scalar(
            select(QualityEvaluation).where(QualityEvaluation.rule_id == UUID(rule["id"]))
        )
        job.deadline = datetime.now(UTC) - timedelta(seconds=1)

    class Forbidden:
        def execute(self, *args):
            pytest.fail("Expired work started a request")

    assert (
        run_quality_one(sessions, quality_setup[2], execution_enabled=True, executor=Forbidden())[
            "status"
        ]
        == "missing"
    )
    row = quality_rows(api, tenant, quality_setup, rule)[0]
    assert not row["request_reserved"]


def test_internal_quality_cannot_be_forged_by_ingestion(api, tenant, quality_setup):
    from test_workflow_traces import result as asserted

    from cutback.server.trace_db import ExternalEvaluation

    rule = create_quality(api, quality_setup)
    span = captured(api, tenant, quality_setup, output="")
    sessions = api[2].state.sessions
    run_quality_one(sessions, quality_setup[2], execution_enabled=True)
    row = quality_rows(api, tenant, quality_setup, rule)[0]
    forged = asserted(
        result_id=row["id"],
        source="cutback-online-quality",
        evaluator=rule["key"],
        evaluator_version=rule["id"],
        trace_id=span["trace_id"],
        span_id=span["span_id"],
        score=True,
        outcome="passed",
    )
    for injected in ("source_origin", "evidence"):
        assert (
            api[0]
            .post(
                "/v1/ingest/evaluations",
                headers=tenant[1],
                json={"results": [{**forged, injected: "cutback_technical_contract"}]},
            )
            .status_code
            == 422
        )
    assert (
        api[0]
        .post("/v1/ingest/evaluations", headers=tenant[1], json={"results": [forged]})
        .status_code
        == 200
    )
    with sessions() as session:
        origins = session.scalars(
            select(ExternalEvaluation.source_origin).where(
                ExternalEvaluation.project_id == UUID(tenant[0]),
                ExternalEvaluation.result_id == UUID(row["id"]),
            )
        ).all()
        assert sorted(origins) == ["cutback_technical_contract", "external"]
    run_online_one(sessions, enabled=True)
    receipts = (
        api[0]
        .get(
            quality_setup[0] + f"/online-rules/{rule['online_rule_id']}/evaluations",
            headers=tenant[2],
        )
        .json()["items"]
    )
    assert receipts[0]["state"] == "failed"


def test_observed_cohorts_keep_missing_features_and_unknown_coverage(api, tenant, quality_setup):
    rule = create_quality(api, quality_setup, record_input_features=True)
    captured(api, tenant, quality_setup, output="")
    captured(api, tenant, quality_setup, output="synthetic answer")
    sessions = api[2].state.sessions
    for _ in range(2):
        run_quality_one(sessions, quality_setup[2], execution_enabled=True)
    now = datetime.now(UTC)
    with sessions.begin() as session:
        rows = session.scalars(
            select(QualityEvaluation).where(QualityEvaluation.rule_id == UUID(rule["id"]))
        ).all()
        for row in rows:
            row.occurred_at = now - timedelta(hours=2 if row.state == "passed" else 1)
    request = dict(
        baseline_start=(now - timedelta(hours=3)).isoformat(),
        baseline_end=(now - timedelta(minutes=90)).isoformat(),
        current_start=(now - timedelta(minutes=90)).isoformat(),
        current_end=now.isoformat(),
        group_by="input_size_bucket",
    )
    endpoint = quality_setup[0] + f"/quality-rules/{rule['id']}/compare"
    response = api[0].post(endpoint, headers=tenant[2], json=request)
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["failure_fraction_delta"] == "1"
    assert report["known_category_total_variation"] == "0"
    assert report["population_coverage"] == "unknown"
    response = api[0].post(endpoint, headers=tenant[2], json={**request, "group_by": "release"})
    assert response.json()["known_category_total_variation"] is None
    assert response.json()["baseline"]["groups"][0]["value"] is None
    assert (
        api[0]
        .post(
            endpoint,
            headers=tenant[2],
            json={**request, "current_start": request["baseline_start"]},
        )
        .status_code
        == 422
    )
