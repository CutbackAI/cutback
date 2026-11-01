"""Scoped encrypted policies through real PostgreSQL and pinned closed SDK transports."""

from uuid import UUID, uuid4

import pytest
from integration.test_learned_routing_workflow import prepared as source_data
from integration.test_learned_routing_workflow import queued_run
from sqlalchemy import select

from cutback.core.improvement_policy import SourceImprovementPolicy
from cutback.core.policy_execution import SourcePolicyExecutionSpec
from cutback.core.policy_producer import PolicySourceBinding
from cutback.evals.experiments import ExperimentPlan, ModelSpec
from cutback.evals.policy_models import policy_descriptors
from cutback.server.db import AuditEvent, CapturePolicyRecord, PayloadSample
from cutback.server.execution_db import ExecutionCall
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


def prepared(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    *,
    phase="evaluation",
    cached=True,
    routed=True,
    binding=None,
    current_routed=False,
):
    base, store, dataset, policy_id, _, executor, calls = source_data(
        api, tenant, tmp_path, monkeypatch
    )
    monkeypatch.setenv("CUTBACK_CACHE_ENABLED", "true")
    original = ModelSpec(
        key="reference",
        provider="openai",
        model="gpt-4.1",
        instruction="OWNED_PRIVATE_POLICY_INSTRUCTION",
        max_output_tokens=64,
    )
    reference = SourceImprovementPolicy(
        workflow="routing-task",
        max_input_bytes=512,
        baseline=original,
        strategy=original,
    )
    cheap = original.model_copy(update={"key": "cheap", "model": "gpt-4.1-mini"})
    strategy = (
        ModelSpec(
            key="sized",
            provider="router",
            model="input-size-v1",
            routes=[
                {"max_input_bytes": 100, "target": cheap},
                {"max_input_bytes": 512, "target": original},
            ],
        )
        if phase == "evaluation" and routed
        else cheap
    )
    candidate = SourceImprovementPolicy(
        workflow=reference.workflow,
        max_input_bytes=512,
        baseline=original,
        strategy=strategy,
        cache_execution={
            "freshness_ref": "owned-source-policy-fixture",
            "ttl_seconds": 60,
            "content_retention_authorized": True,
            "immutable_or_safely_invalidated": True,
            "operation_cost_upper_usd": "0.000001",
            "operation_cost_basis_ref": "owned-synthetic",
        }
        if cached and phase != "training"
        else None,
    )
    imports_policies = [("reference", reference), ("candidate", candidate)]
    if current_routed:
        assert not cached and phase == "evaluation" and not routed

        # An existing router with two actual current native instructions. Its
        # historical anchor is never substituted for the reference request.
        current_target = original.model_copy(
            update={"key": "current-short", "instruction": "OWNED_CURRENT_ROUTED_INSTRUCTION"}
        )
        reference.strategy = ModelSpec(
            provider="router",
            key="current-router",
            model="input-size-v1",
            routes=[
                {"max_input_bytes": 100, "target": current_target},
                {"max_input_bytes": 512, "target": original},
            ],
        )
        control = SourceImprovementPolicy.model_validate(
            {**reference.model_dump(mode="json"), "strategy": original.model_dump(mode="json")}
        )
        imports_policies = [
            ("reference", reference),
            ("candidate", candidate),
            ("native-current-tail", control),
        ]
    source = binding or PolicySourceBinding(
        producer_file="app.py",
        producer_sha256="a" * 64,
        configuration_file="policy.json",
        configuration_sha256="b" * 64,
        policy_sha256=reference.policy_sha256,
        source_tree_hash="c" * 64,
        commit="d" * 40,
        workflow=reference.workflow,
        max_input_bytes=512,
    )
    references, imports = [], []
    for key, policy in imports_policies:
        body = {
            "purpose": "owned-synthetic-test",
            "request_id": str(uuid4()),
            "dataset_id": dataset["id"],
            "source": source.model_dump(mode="json"),
            "key": key,
            "policy": policy.model_dump(mode="json"),
        }
        response = api[0].post(base + "/source-policies", headers=api[1], json=body)
        assert response.status_code == 201, response.text
        references.append(response.json())
        imports.append(body)
    execution = SourcePolicyExecutionSpec(
        source=source,
        reference=references[0],
        candidates=references[1:],
        transport="scripted_test_only",
        phase=phase,
    )
    models = policy_descriptors(execution, dict(imports_policies))
    plan = ExperimentPlan(
        name="owned-policy-comparison",
        workflow=reference.workflow,
        dataset_hash=dataset["immutable_hash"],
        baseline=models["reference"],
        candidates=[models[key] for key, _ in imports_policies[1:]],
        metric={
            "kind": "exact_label",
            "minimum": 1,
            "max_regression": 0,
            "min_test_samples": 3,
            "min_test_groups": 3,
        },
        budget={"max_usd": "5", "max_requests": 600, "max_wall_seconds": 300},
        approved_providers=["openai"],
        evaluator_version="deterministic-v19",
        source_policy_execution=execution,
        split="train" if phase == "training" else "validation",
        retain_case_outputs=True,
        content_nonce=uuid4(),
    )
    response = api[0].post(
        base + "/experiments/plans",
        headers=api[1],
        json={
            "dataset_id": dataset["id"],
            "specification": plan.model_dump(mode="json"),
        },
    )
    assert response.status_code == 201, response.text
    return base, store, dataset, policy_id, executor, calls, plan, response.json(), imports


def test_encrypted_idempotent_import_scoped_admin_authority_and_content_free_metadata(
    api, tenant, tmp_path, monkeypatch
):
    base, store, _, _, executor, calls, plan, saved, imports = prepared(
        api, tenant, tmp_path, monkeypatch
    )
    try:
        response = api[0].post(base + "/source-policies", headers=api[1], json=imports[0])
        assert response.status_code == 201
        assert response.json() == plan.source_policy_execution.reference.model_dump(mode="json")
        assert (
            api[0].post(base + "/source-policies", headers=tenant[2], json=imports[0]).status_code
            == 403
        )
        changed = {**imports[0], "key": "changed-key"}
        assert (
            api[0].post(base + "/source-policies", headers=api[1], json=changed).status_code == 409
        )
        other = (
            api[0]
            .post("/v1/projects", headers=api[1], json={"slug": "owned-other-" + uuid4().hex})
            .json()["id"]
        )
        assert (
            api[0]
            .post(f"/v1/projects/{other}/source-policies", headers=api[1], json=imports[0])
            .status_code
            == 409
        )
        metadata = api[0].get(base + "/experiments/plans", headers=tenant[2])
        assert metadata.status_code == 200 and "OWNED_PRIVATE" not in metadata.text
        assert str(plan.source_policy_execution.reference.payload_id) not in metadata.text
        with api[2].state.sessions() as session:
            sample = session.get(PayloadSample, plan.source_policy_execution.reference.payload_id)
            assert "OWNED_PRIVATE" not in str(sample.metadata_safe)
            assert b"OWNED_PRIVATE" not in (store.root / tenant[0] / sample.object_ref).read_bytes()
            audits = session.scalars(
                select(AuditEvent).where(
                    AuditEvent.project_id == UUID(tenant[0]),
                    AuditEvent.action == "source_policy_import",
                )
            ).all()
            assert len(audits) == 3 and all(
                "OWNED_PRIVATE" not in str(row.details) for row in audits
            )
        assert calls == [] and saved["evaluator_version"] == "deterministic-v19"
    finally:
        executor.close()


def test_real_native_routed_cache_leaves_share_canonical_pre_call_reservations(
    api, tenant, tmp_path, monkeypatch
):
    base, store, _, _, executor, calls, plan, saved, _ = prepared(
        api, tenant, tmp_path, monkeypatch
    )
    try:
        run_id = queued_run(api, base, saved)
        result = run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=run_id,
        )
        assert result["status"] == "COMPLETED", result.get("results", {}).get("code")
        report = result["results"]
        assert report["billing_complete"] is True and report["requests"] == len(calls)
        assert report["models"][1]["exact_cache"]["cache_operations"] > 0
        assert report["synthetic_execution"]["real_model_quality_verified"] is False
        with api[2].state.sessions() as session:
            rows = session.scalars(
                select(ExecutionCall).where(ExecutionCall.run_id == run_id)
            ).all()
            assert len(rows) == len(calls) and all(row.state == "completed" for row in rows)
            assert all(
                row.model_key.startswith("policy-") and row.purpose == "evaluation" for row in rows
            )
            assert any(row.cache_observation is not None for row in rows)
            assert all(row.spent_usd <= row.reserved_usd for row in rows)
    finally:
        executor.close()


def test_training_is_separate_optimization_usage_and_never_claims_heldout_quality(
    api, tenant, tmp_path, monkeypatch
):
    base, store, _, _, executor, calls, _, saved, _ = prepared(
        api, tenant, tmp_path, monkeypatch, phase="training", cached=False
    )
    try:
        run_id = queued_run(api, base, saved)
        result = run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=run_id,
        )
        assert result["status"] == "COMPLETED", result.get("results", {}).get("code")
        report = result["results"]
        assert report["recommendations"] == [] and report["requests"] == len(calls)
        assert report["source_policy_execution"]["heldout_quality_claim"] is False
        with api[2].state.sessions() as session:
            rows = session.scalars(
                select(ExecutionCall).where(ExecutionCall.run_id == run_id)
            ).all()
            assert rows and all(row.purpose == "optimization" for row in rows)
    finally:
        executor.close()


def test_revoked_policy_between_approval_and_worker_prevents_all_native_dispatch(
    api, tenant, tmp_path, monkeypatch
):
    base, store, _, policy_id, executor, calls, _, saved, _ = prepared(
        api, tenant, tmp_path, monkeypatch
    )
    try:
        run_id = queued_run(api, base, saved)
        with api[2].state.sessions.begin() as session:
            session.get(CapturePolicyRecord, UUID(policy_id)).enabled = False
        result = run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=run_id,
        )
        assert result["status"] == "FAILED" and calls == []
        with api[2].state.sessions() as session:
            assert (
                session.scalar(select(ExecutionCall.id).where(ExecutionCall.run_id == run_id))
                is None
            )
    finally:
        executor.close()


def test_train_only_fit_authenticates_native_aliases_and_preserves_the_whole_source_domain(
    api, tenant, tmp_path, monkeypatch
):
    from cutback.evals.learned_routing import RoutingFitOptions
    from cutback.server.policy_training import fit_policy
    from cutback.server.routing_service import authenticated_training

    base, store, _, _, executor, calls, plan, saved, _ = prepared(
        api, tenant, tmp_path, monkeypatch, phase="training", cached=False
    )
    try:
        run_id = queued_run(api, base, saved)
        result = run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=run_id,
        )
        assert result["status"] == "COMPLETED", result.get("results", {}).get("code")
        count = len(calls)
        with api[2].state.sessions.begin() as session:
            candidate, private_fit, _, _, _ = fit_policy(
                session,
                store,
                UUID(tenant[0]),
                run_id,
                RoutingFitOptions(min_leaf_cases=2, min_training_groups=3),
            )
            assert candidate.strategy.routes[-1].max_input_bytes == 512
            assert candidate.baseline.model == "gpt-4.1" and candidate.cache_execution is None
            assert candidate.request("owned short")["model"] == "gpt-4.1-mini"
            assert candidate.request("x" * 150)["model"] == "gpt-4.1"
            assert private_fit["heldout_quality_claim"] is False
            assert private_fit["fit_compute_usd"] is None
            with pytest.raises(ValueError, match="EXPLICIT_TRAIN_ONLY_SOURCE"):
                authenticated_training(session, store, UUID(tenant[0]), run_id)
            call = session.scalar(select(ExecutionCall).where(ExecutionCall.run_id == run_id))
            original = call.model_key
            call.model_key = plan.baseline.key
            session.flush()
            with pytest.raises(ValueError, match="CALL_RECEIPT_CHANGED_OR_UNCERTAIN"):
                authenticated_training(session, store, UUID(tenant[0]), run_id, source_policy=True)
            call.model_key = original
        assert len(calls) == count
    finally:
        executor.close()
