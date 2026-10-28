from uuid import UUID

import pytest
from sqlalchemy import select
from support_evaluation import SyntheticExecutor, plan_for
from test_prompt_registry import create_prompt, post_version, version_body
from test_prompt_registry import prompt_setup as prompt_setup

from cutback.core.prompts import PromptDocument, PromptGraph
from cutback.server.db import AccessKey, DatasetRecord, ExperimentPlanRecord
from cutback.server.prompt_db import PromptExperimentUse
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


def prepare(client, app, admin, base, policy, author):
    prompt = create_prompt(client, author, base)
    body = version_body(policy)
    version = post_version(client, author, base, prompt, body).json()
    graph = PromptGraph(
        UUID(version["id"]), {UUID(version["id"]): PromptDocument.model_validate(body["document"])}
    )
    with app.state.sessions() as session:
        dataset = session.scalar(
            select(DatasetRecord).where(DatasetRecord.project_id == UUID(base.rsplit("/", 1)[1]))
        )
        spec = plan_for({"immutable_hash": dataset.immutable_hash}).model_dump(mode="json")
        dataset_id = str(dataset.id)
    spec["candidates"][0]["request_contract"] = graph.native_contract(
        {}, surface="chat.completions.create"
    ).model_dump(mode="json")
    spec["prompt_bindings"] = [
        {
            "model_key": "candidate",
            "version_id": version["id"],
            "version_hash": version["document_hash"],
            "environment": "staging",
            "prompt_variables": {},
        }
    ]
    response = client.post(
        base + "/experiments/plans",
        headers=author,
        json={"dataset_id": dataset_id, "specification": spec},
    )
    assert response.status_code == 201, response.text
    plan = response.json()
    approved = client.post(
        base + "/experiments/plans/" + plan["id"] + "/approve",
        headers=admin,
        json={"plan_hash": plan["plan_hash"], "approval_ref": "synthetic-no-paid-vendor"},
    )
    assert approved.status_code == 200, approved.text
    return plan, spec, dataset_id


def test_frozen_prompt_binding_compares_exact_contract_and_revocation_blocks_queue(
    api, tenant, prompt_setup
):
    client, admin, app = api
    base, policy, creds = prompt_setup
    plan, spec, dataset = prepare(client, app, admin, base, policy, creds[0][1])
    with app.state.sessions() as session:
        row = session.get(ExperimentPlanRecord, UUID(plan["id"]))
        assert "OWNED_PRIVATE_INSTRUCTION" not in str(row.specification)
        assert (
            session.scalar(
                select(PromptExperimentUse).where(PromptExperimentUse.plan_id == row.id)
            ).actor
            == creds[0][0]
        )
    spec["candidates"][0]["request_contract"]["payload"] = {"op": "literal", "value": "tampered"}
    assert (
        client.post(
            base + "/experiments/plans",
            headers=creds[0][1],
            json={"dataset_id": dataset, "specification": spec},
        ).status_code
        == 409
    )
    with app.state.sessions.begin() as session:
        session.get(AccessKey, UUID(creds[0][0])).role = "viewer"
    queued = client.post(base + "/experiments", headers=admin, json={"plan_id": plan["id"]})
    assert queued.status_code == 403, queued.text
    read = client.post(
        base + "/experiments/plans/" + plan["id"] + "/content",
        headers=admin,
        json={"purpose": "revoked-prompt-copy"},
    )
    assert read.status_code == 403, read.text


def test_prompt_policy_rechecked_between_calls_and_no_automatic_retry(api, tenant, prompt_setup):
    client, admin, app = api
    base, policy, creds = prompt_setup
    plan, _, _ = prepare(client, app, admin, base, policy, creds[0][1])
    queued = client.post(base + "/experiments", headers=admin, json={"plan_id": plan["id"]})
    assert queued.status_code == 202, queued.text

    class RevokingExecutor(SyntheticExecutor):
        def execute(self, model, text, timeout_seconds):
            result = super().execute(model, text, timeout_seconds)
            with app.state.sessions.begin() as session:
                from datetime import UTC, datetime

                session.get(AccessKey, UUID(creds[0][0])).revoked_at = datetime.now(UTC)
            return result

    executor = RevokingExecutor()
    result = run_one(
        app.state.sessions,
        app.state.artifact_store,
        executor=executor,
        execution_enabled=True,
        only_run_id=UUID(queued.json()["id"]),
    )
    assert result["status"] == "FAILED" and len(executor.calls) == 1
    assert (
        run_one(
            app.state.sessions,
            app.state.artifact_store,
            executor=executor,
            execution_enabled=True,
            only_run_id=UUID(queued.json()["id"]),
        )["status"]
        == "IDLE"
    )
    assert len(executor.calls) == 1


def test_playground_compiles_without_inference_and_freezes_the_selected_version(
    api, tenant, prompt_setup
):
    client, admin, app = api
    base, policy, creds = prompt_setup
    prompt = create_prompt(client, creds[0][1], base)
    body = version_body(policy)
    version = post_version(client, creds[0][1], base, prompt, body).json()
    with app.state.sessions() as session:
        dataset = session.scalar(
            select(DatasetRecord).where(DatasetRecord.project_id == UUID(tenant[0]))
        )
        spec = plan_for({"immutable_hash": dataset.immutable_hash}).model_dump(mode="json")
        dataset_id = str(dataset.id)
    response = client.post(
        base + "/prompts/playground",
        headers=creds[0][1],
        json={
            "dataset_id": dataset_id,
            "specification": spec,
            "selections": [
                {"model_key": "candidate", "version_id": version["id"], "environment": "staging"}
            ],
            "purpose": "owned-synthetic-plan",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "READY_FOR_APPROVAL"
    with app.state.sessions() as session:
        row = session.get(ExperimentPlanRecord, UUID(response.json()["id"]))
        from cutback.server.plan_artifacts import load_plan

        frozen = load_plan(app.state.artifact_store, UUID(tenant[0]), row.specification)
        assert frozen.prompt_bindings[0].version_id == UUID(version["id"])
        assert frozen.candidates[0].request_contract.render("example")["messages"] == [
            {"role": "user", "content": "OWNED_PRIVATE_INSTRUCTION: example"}
        ]
        assert row.approval_status == "pending"


def test_prompt_confirmation_is_bound_to_completed_test_run_and_preserves_synthetic_limits(
    api, tenant, prompt_setup
):
    client, admin, app = api
    base, policy, creds = prompt_setup
    plan, spec, _ = prepare(client, app, admin, base, policy, creds[0][1])
    queued = client.post(base + "/experiments", headers=admin, json={"plan_id": plan["id"]})
    assert queued.status_code == 202, queued.text
    result = run_one(
        app.state.sessions,
        app.state.artifact_store,
        executor=SyntheticExecutor(),
        execution_enabled=True,
        only_run_id=queued.json()["id"],
    )
    assert result["status"] == "COMPLETED", result
    version_id = spec["prompt_bindings"][0]["version_id"]
    from cutback.server.prompt_db import PromptVersion

    with app.state.sessions() as session:
        prompt_id = str(session.get(PromptVersion, UUID(version_id)).prompt_id)
    promote = {
        "version_id": version_id,
        "environment": "staging",
        "alias": "reviewed",
        "expected_revision": 0,
        "approval_ref": "synthetic-bound-run",
        "quality_unverified_acknowledged": False,
        "evidence_run_id": queued.json()["id"],
        "evidence_model_key": "candidate",
    }
    path = base + "/prompts/" + prompt_id + "/aliases"
    denied = client.post(path, headers=admin, json=promote)
    assert denied.status_code == 409, denied.text
    accepted = client.post(
        path, headers=admin, json={**promote, "quality_unverified_acknowledged": True}
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["receipt"]["quality_status"] == "sample_checks_quality_unverified"
    with app.state.sessions() as session:
        row = session.get(ExperimentPlanRecord, UUID(plan["id"]))
        app.state.artifact_store.delete(UUID(tenant[0]), row.specification["artifact_ref"])
    unavailable = client.post(
        path,
        headers=admin,
        json={**promote, "alias": "missing-artifact", "quality_unverified_acknowledged": True},
    )
    assert (
        unavailable.status_code == 409
        and unavailable.json()["code"] == "PROMPT_CONFIRMATION_ARTIFACT_UNAVAILABLE"
    )


def test_output_schema_adds_required_frozen_gate_and_cannot_be_dropped(api, tenant, prompt_setup):
    from cutback.evals.metrics import evaluate
    from cutback.server.plan_artifacts import load_plan

    client, admin, app = api
    base, policy, creds = prompt_setup
    prompt = create_prompt(client, creds[0][1], base)
    schema = {
        "type": "object",
        "properties": {"label": {"type": "string"}},
        "required": ["label"],
        "additionalProperties": False,
    }
    document = {
        "text": "Label {{input}} using {{language}}.",
        "variables": {"input": "string", "language": "string"},
        "output_schema": schema,
    }
    version = post_version(client, creds[0][1], base, prompt, version_body(policy, document)).json()
    with app.state.sessions() as session:
        dataset = session.scalar(
            select(DatasetRecord).where(DatasetRecord.project_id == UUID(tenant[0]))
        )
        spec = plan_for(
            {"immutable_hash": dataset.immutable_hash},
            baseline={"key": "baseline", "provider": "openai", "model": "gpt-4.1"},
            candidates=[{"key": "candidate", "provider": "openai", "model": "gpt-4.1-mini"}],
            approved_providers=["openai"],
        ).model_dump(mode="json")
        dataset_id = str(dataset.id)
    response = client.post(
        base + "/prompts/playground",
        headers=creds[0][1],
        json={
            "dataset_id": dataset_id,
            "specification": spec,
            "selections": [
                {
                    "model_key": "baseline",
                    "version_id": version["id"],
                    "environment": "staging",
                    "prompt_variables": {"language": "FR"},
                },
                {
                    "model_key": "candidate",
                    "version_id": version["id"],
                    "environment": "staging",
                    "prompt_variables": {"language": "FR"},
                },
            ],
            "purpose": "synthetic-schema-check",
        },
    )
    assert response.status_code == 201, response.text
    with app.state.sessions() as session:
        row = session.get(ExperimentPlanRecord, UUID(response.json()["id"]))
        frozen = load_plan(app.state.artifact_store, UUID(tenant[0]), row.specification)
        assert "FR" not in str(row.specification)
        from cutback.evals.datasets import digest

        assert digest({"language": "FR"}) not in str(row.specification)
        assert digest(schema) not in str(row.specification)
        assert str(frozen.prompt_bindings[0].content_nonce) not in str(row.specification)
    check = frozen.criteria[-1]
    assert check.required and check.metric.schema_definition == schema
    evaluated = evaluate(
        [{}, {}, {}, {}],
        ['{"label":"A"}', '{"label":"B"}', '{"label":2}', '{"label":"A","extra":true}'],
        check.metric,
        independent_labels=False,
    )
    assert evaluated["value"] == 0.5
    weakened = frozen.model_dump(mode="json")
    weakened["criteria"] = []
    rejected = client.post(
        base + "/experiments/plans",
        headers=creds[0][1],
        json={"dataset_id": dataset_id, "specification": weakened},
    )
    assert rejected.status_code == 422, rejected.text


def test_bound_schedule_rechecks_prompt_before_reserving_or_exposing_test_groups(
    api, tenant, prompt_setup
):
    from datetime import UTC, datetime, timedelta

    from cutback.server.continuous import queue_schedules
    from cutback.server.enterprise_db import ExperimentSchedule

    client, admin, app = api
    base, policy, creds = prompt_setup
    plan, _, _ = prepare(client, app, admin, base, policy, creds[0][1])
    now = datetime.now(UTC)
    response = client.post(
        base + "/schedules",
        headers=admin,
        json={
            "plan_id": plan["id"],
            "interval_seconds": 60,
            "max_runs": 2,
            "max_total_usd": "2",
            "max_total_requests": 1000,
            "first_run_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat(),
        },
    )
    assert response.status_code == 201, response.text
    schedule = response.json()
    approved = client.post(
        base + "/schedules/" + schedule["id"] + "/approve",
        headers=admin,
        json={"approval_hash": schedule["approval_hash"], "approval_ref": "synthetic-schedule"},
    )
    assert approved.status_code == 200, approved.text
    with app.state.sessions.begin() as session:
        session.get(AccessKey, UUID(creds[0][0])).revoked_at = now
    outcome = queue_schedules(
        app.state.sessions,
        enabled=True,
        now=now + timedelta(seconds=1),
        store=app.state.artifact_store,
    )
    assert outcome["queued"] == 0
    with app.state.sessions() as session:
        row = session.get(ExperimentSchedule, UUID(schedule["id"]))
        assert not row.enabled and row.runs_queued == 0 and row.reserved_usd == 0
