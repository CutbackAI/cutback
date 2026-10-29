"""Encrypted corpus -> actual native SDK -> durable PG calls -> scoped report."""

import json
from uuid import UUID

import httpx
import httpx2
import pytest
from sqlalchemy import select
from support_evaluation import authorized_dataset, dataset_body
from support_native_agent import native_executor, response_body
from support_retrieval import fixture

from cutback.evals.experiments import ExperimentPlan
from cutback.server.db import (
    DatasetRecord,
    ExperimentPlanRecord,
    ExperimentRunRecord,
    PayloadSample,
    Project,
)
from cutback.server.execution_db import ExecutionCall
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("revoke_before_start", [True, False])
def test_retrieval_corpus_revocation_export_and_project_deletion(
    api, tenant, tmp_path, monkeypatch, revoke_before_start
):
    base, store, run_id, policy_id, _ = prepare(api, tenant, tmp_path / "encrypted", monkeypatch)
    calls = []

    def revoke():
        result = api[0].delete(base + f"/capture-policies/{policy_id}", headers=api[1])
        assert result.status_code == 200

    def handle(request):
        calls.append(1)
        revoke()
        response = response_body("responses.create")
        response["output"][0]["content"][0]["text"] = "14"
        return httpx.Response(200, json=response)

    if revoke_before_start:
        revoke()
    executor = native_executor("responses.create", monkeypatch, handle)
    try:
        result = run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=run_id,
        )
        assert result["status"] == "FAILED" and len(calls) == (0 if revoke_before_start else 1)
    finally:
        executor.close()
    with api[2].state.sessions() as session:
        run = session.get(ExperimentRunRecord, run_id)
        plan = session.get(ExperimentPlanRecord, run.plan_id)
        ref = plan.specification["artifact_ref"]
        plan_id = str(plan.id)
        slug = session.get(Project, UUID(tenant[0])).slug
    assert (
        api[0]
        .post(
            base + f"/experiments/plans/{plan_id}/content",
            headers=api[1],
            json={"purpose": "revoked-read-negative"},
        )
        .status_code
        == 403
    )
    metadata = api[0].get(base + "/export", headers=api[1])
    assert metadata.status_code == 200 and b"fourteen-days" not in metadata.content
    resolved = api[0].get(base + f"/experiments/plans/{plan_id}/execution", headers=tenant[2])
    assert resolved.status_code == 200 and resolved.json()["run_id"] == str(run_id)
    other = (
        api[0]
        .post("/v1/projects", headers=api[1], json={"slug": "retrieval-isolation-" + policy_id.hex})
        .json()["id"]
    )
    assert (
        api[0]
        .get(f"/v1/projects/{other}/experiments/plans/{plan_id}/execution", headers=api[1])
        .status_code
        == 404
    )
    deleted = api[0].delete(base, headers=api[1], params={"confirm_slug": slug})
    assert deleted.status_code == 200, deleted.text
    with pytest.raises(OSError):
        store.get(UUID(tenant[0]), ref)


def prepare(
    api, tenant, tmp_path, monkeypatch, *, binding=None, automatic=False, provider="openai"
):
    client, admin, app = api
    base, store, initial, _ = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    with app.state.sessions() as session:
        policy_id = session.get(DatasetRecord, UUID(initial["id"])).provenance["policy_id"]
    data = dataset_body(12).model_dump(mode="json")
    if provider == "anthropic":
        from cutback.server.db import CapturePolicyRecord

        with app.state.sessions() as session:
            policy = session.get(CapturePolicyRecord, UUID(policy_id)).policy
        response = client.post(
            base + "/capture-policies",
            headers=admin,
            json={**policy, "allowed_providers": ["anthropic"]},
        )
        assert response.status_code == 201
        policy_id = response.json()["id"]
    data["policy_id"] = policy_id
    for i, case in enumerate(data["cases"]):
        case.update(
            input=f"refund return deadline sample-{chr(65 + i)}",
            gold="14",
            reference="evaluator-only-reference-canary",
        )
    response = client.post(base + "/datasets", headers=admin, json=data)
    assert response.status_code == 201, response.text
    dataset = response.json()
    spec, _ = fixture()
    specification = ExperimentPlan.model_validate(
        {
            **spec.model_dump(mode="json"),
            "workflow": "classify",
            "dataset_hash": dataset["immutable_hash"],
            "budget": {**spec.budget.model_dump(mode="json"), "max_requests": 100},
            "retrieval_execution": {
                **spec.retrieval_execution.model_dump(mode="json"),
                "configuration_binding": binding.model_dump(mode="json") if binding else None,
            },
        }
    )
    if provider == "anthropic":
        raw = specification.model_dump(mode="json")
        for model in [raw["baseline"], *raw["candidates"]]:
            model.update(provider="anthropic", model="claude-haiku-4-5")
        raw["approved_providers"] = ["anthropic"]
        specification = ExperimentPlan.model_validate(raw)
    if automatic:
        response = client.post(
            base + "/retrieval/draft",
            headers=admin,
            json={
                "dataset_id": dataset["id"],
                "baseline": specification.baseline.model_dump(mode="json"),
                "corpus": specification.retrieval_execution.corpus.model_dump(mode="json"),
                "settings": specification.retrieval_execution.variants[0].settings.model_dump(
                    mode="json"
                ),
                "metric": specification.metric.model_dump(mode="json"),
                "budget": specification.budget.model_dump(mode="json"),
                "assumed_usd_per_cpu_hour": "0.01",
                "retain_case_outputs": True,
                "configuration_binding": binding.model_dump(mode="json") if binding else None,
            },
        )
    else:
        response = client.post(
            base + "/experiments/plans",
            headers=admin,
            json={
                "dataset_id": dataset["id"],
                "specification": specification.model_dump(mode="json"),
            },
        )
    assert response.status_code == 201, response.text
    plan = response.json()
    assert "fourteen-days" not in response.text
    with app.state.sessions() as session:
        row = session.get(ExperimentPlanRecord, UUID(plan["id"]))
        assert "fourteen-days" not in json.dumps(row.specification)
        assert (
            row.specification["public_specification"]["retrieval_execution"]["content"]
            == "authorized_encrypted_plan_only"
        )
        assert b"fourteen-days" in store.get(UUID(tenant[0]), row.specification["artifact_ref"])
    approved = client.post(
        base + f"/experiments/plans/{plan['id']}/approve",
        headers=admin,
        json={"plan_hash": plan["plan_hash"], "approval_ref": "owned-retrieval-approval"},
    )
    assert approved.status_code == 200, approved.text
    queued = client.post(base + "/experiments", headers=admin, json={"plan_id": plan["id"]})
    assert queued.status_code == 202, queued.text
    return base, store, UUID(queued.json()["id"]), UUID(policy_id), specification


@pytest.mark.parametrize("mode", ["success", "missing_usage", "outage"])
@pytest.mark.parametrize("surface", ["responses.create", "messages.create"])
def test_native_retrieval_run_preserves_privacy_shared_receipts_and_unknown_billing(
    api, tenant, tmp_path, monkeypatch, mode, surface
):
    provider = "anthropic" if surface == "messages.create" else "openai"
    transport = httpx2 if provider == "anthropic" else httpx
    base, store, run_id, policy_id, specification = prepare(
        api, tenant, tmp_path, monkeypatch, provider=provider
    )
    calls = []

    def handle(request):
        with api[2].state.sessions() as session:
            receipt = session.scalar(
                select(ExecutionCall)
                .where(ExecutionCall.run_id == run_id)
                .order_by(ExecutionCall.sequence.desc())
            )
            assert receipt is not None and receipt.state == "reserved"
        body = json.loads(request.content)
        text = body["input"] if provider == "openai" else body["messages"][0]["content"]
        assert "evaluator-only-reference-canary" not in text
        if provider == "openai":
            assert body["store"] is False
        frame = json.loads(text[text.index("{") :])
        calls.append(frame)
        if mode == "outage":
            raise transport.ConnectError("owned synthetic interruption")
        response = response_body(surface)
        target = (
            response["output"][0]["content"][0] if provider == "openai" else response["content"][0]
        )
        target["text"] = (
            "14" if any("fourteen-days" in row["text"] for row in frame["documents"]) else "wrong"
        )
        response["usage"].update(
            input_tokens=len(text.encode()), output_tokens=2, total_tokens=len(text.encode()) + 2
        )
        if mode == "missing_usage":
            response.pop("usage")
        return transport.Response(200, json=response)

    executor = native_executor(surface, monkeypatch, handle)
    try:
        outcome = run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=run_id,
        )
    finally:
        executor.close()
    with api[2].state.sessions() as session:
        run = session.get(ExperimentRunRecord, run_id)
        assert "fourteen-days" not in json.dumps(run.results)
        records = list(session.scalars(select(ExecutionCall).where(ExecutionCall.run_id == run_id)))
        assert len(records) == len(calls) == run.requests_used
        if mode == "success":
            assert outcome["status"] == "COMPLETED", outcome
            assert run.known_cost_requests == len(calls)
            decisions = {r["candidate"]["key"]: r for r in run.results["recommendations"]}
            assert decisions["compact"]["retrieval_automation_eligible"] is True
            assert decisions["unsafe"]["retrieval_automation_eligible"] is False
            samples = list(
                session.scalars(
                    select(PayloadSample).where(
                        PayloadSample.metadata_safe["run_id"].as_string() == str(run_id)
                    )
                )
            )
            assert samples
            content = json.loads(store.get(UUID(tenant[0]), samples[0].object_ref))
            assert content["retrieval"]["documents"]
        else:
            assert outcome["status"] == "FAILED" and len(calls) == 1
            assert run.reserved_usd > 0 and not run.results["billing_complete"]
    assert (
        api[0]
        .post(
            base + f"/experiments/{run_id}/cases/paired",
            headers=tenant[2],
            json={"purpose": "synthetic-retrieval-review"},
        )
        .status_code
        == 403
    )


def test_automatic_retrieval_search_confirmation_source_diff_and_protected_holdout(
    api, tenant, tmp_path, monkeypatch
):
    from support_retrieval_application import application, git

    from cutback.retrieval_changes import RetrievalProposal, inspect_configuration, prepare_patch

    root = application(tmp_path)
    _, binding = inspect_configuration(root, "retrieval.json")
    base, store, validation_id, _, _ = prepare(
        api, tenant, tmp_path / "store", monkeypatch, binding=binding, automatic=True
    )
    calls = []

    def handle(request):
        text = json.loads(request.content)["input"]
        calls.append(text)
        body = response_body("responses.create")
        body["output"][0]["content"][0]["text"] = "14" if "fourteen-days" in text else "wrong"
        body["usage"].update(
            input_tokens=len(text.encode()), output_tokens=2, total_tokens=len(text.encode()) + 2
        )
        return httpx.Response(200, json=body)

    executor = native_executor("responses.create", monkeypatch, handle)
    try:
        outcome = run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=validation_id,
        )
        assert outcome["status"] == "COMPLETED", outcome
        # Server selects one candidate; clients cannot nominate a different settings object.
        response = api[0].post(
            base + "/retrieval/confirmation",
            headers=api[1],
            json={
                "validation_run_id": str(validation_id),
                "budget": {"max_usd": "1", "max_requests": 100},
            },
        )
        assert response.status_code == 201, response.text
        confirmation = response.json()
        assert confirmation["split"] == "test" and confirmation["approval_required"]
        with api[2].state.sessions() as session:
            row = session.get(ExperimentPlanRecord, UUID(confirmation["id"]))
            from cutback.server.plan_artifacts import load_plan

            spec = load_plan(store, UUID(tenant[0]), row.specification)
        forged = spec.model_dump(mode="json")
        forged["retrieval_execution"]["corpus"]["documents"][0]["text"] += " changed"
        denied = api[0].post(
            base + "/experiments/plans",
            headers=api[1],
            json={"dataset_id": str(row.dataset_id), "specification": forged},
        )
        assert denied.status_code == 409, denied.text
        approved = api[0].post(
            base + f"/experiments/plans/{confirmation['id']}/approve",
            headers=api[1],
            json={
                "plan_hash": confirmation["plan_hash"],
                "approval_ref": "synthetic-confirmation-only",
            },
        )
        assert approved.status_code == 200
        queued = api[0].post(
            base + "/experiments", headers=api[1], json={"plan_id": confirmation["id"]}
        )
        assert queued.status_code == 202, queued.text
        confirmation_id = UUID(queued.json()["id"])
        result = run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=confirmation_id,
        )
        assert result["status"] == "COMPLETED", result
    finally:
        executor.close()
    endpoint = base + f"/retrieval/{confirmation_id}/proposal"
    response = api[0].post(endpoint, headers=api[1], json={"purpose": "owned-synthetic-review"})
    assert response.status_code == 200, response.text
    assert "fourteen-days" not in response.text
    proposal = RetrievalProposal.model_validate(response.json())
    assert proposal.decision == "synthetic_local_review_only"
    artifact = prepare_patch(root, proposal)
    assert artifact["application_changed"] is False
    patch = tmp_path / "retrieval.patch"
    patch.write_text(artifact["diff"], encoding="utf-8", newline="")
    git(root, "apply", "--check", str(patch))
    assert api[0].post(endpoint, headers=tenant[2], json={"purpose": "viewer"}).status_code == 403
    repeated = api[0].post(
        base + "/retrieval/confirmation",
        headers=api[1],
        json={
            "validation_run_id": str(validation_id),
            "budget": {"max_usd": "1", "max_requests": 100},
        },
    )
    assert repeated.status_code == 409, repeated.text
    (root / "app.py").write_bytes((root / "app.py").read_bytes() + b"\n# changed\n")
    with pytest.raises(ValueError, match="CLEAN_TRACKED|SOURCE_CHANGED"):
        prepare_patch(root, proposal)
