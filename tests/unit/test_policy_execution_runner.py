"""Whole-policy dispatch, reservation identities, current authority and uncertain calls."""

from decimal import Decimal
from uuid import uuid4

import pytest
from test_policy_execution_artifacts import artifacts
from test_policy_execution_plan import plan

from cutback.evals.experiments import Execution, ExperimentPlan, plan_summary, run, upper_cost


def prepared(*, training=False):
    execution, reference, candidate = artifacts()
    value = plan().model_dump(mode="json")
    execution.transport = "scripted_test_only"
    if training:
        # Training data gathers native teacher receipts, never cache hits or a
        # validation/test choice. A separate valid native candidate is required.
        from test_source_improvement_policy import policy

        from cutback.evals.policy_artifacts import prepare_policy_payload

        native = policy()
        native.strategy.instruction = "OWNED_PRIVATE_TRAIN_VARIANT"
        candidate, ref = prepare_policy_payload("routed-cache", uuid4(), native)
        execution.candidates = [ref]
        execution.phase = "training"
        from cutback.evals.policy_artifacts import decode_policy_bundle
        from cutback.evals.policy_models import policy_descriptors

        descriptions = policy_descriptors(
            execution,
            decode_policy_bundle(
                {execution.reference.payload_id: reference, ref.payload_id: candidate}, execution
            ),
        )
        value["candidates"] = [descriptions[ref.key].model_dump(mode="json")]
        value["split"] = "train"
    value["source_policy_execution"] = execution.model_dump(mode="json")
    value = ExperimentPlan.model_validate(value)
    data = {
        "immutable_hash": value.dataset_hash,
        "items": [
            {
                "split": value.split,
                "group_hash": str(index),
                "case": {"id": str(index), "input": text, "gold": "A"},
            }
            for index, text in enumerate(("owned short", "x" * 65))
        ],
    }
    return (
        value,
        data,
        {
            execution.reference.payload_id: reference,
            execution.candidates[0].payload_id: candidate,
        },
    )


class Backend:
    synthetic_transport = True
    exact_cache_ready = True

    def __init__(self, reservations, *, fail=False):
        self.reservations, self.calls, self.fail = reservations, [], fail

    def execute(self, model, text, timeout_seconds):
        assert self.reservations[-1][1] == model
        assert self.reservations[-1][0] == upper_cost(model)
        assert model.provider == "openai" and not model.routes
        self.calls.append((model, text))
        if self.fail:
            raise TimeoutError("OWNED_PRIVATE_EXCEPTION_CONTENT")
        cost = Decimal("0.000001")
        observation = None
        if model.exact_cache is not None:
            cache = model.exact_cache
            observation = {
                "origin": "native_provider",
                "native_sdk_calls": 1,
                "cache_write": "created",
                "cache_operations": 2,
                "provider_modeled_cost_usd": str(cost),
                "cache_operation_cost_upper_usd": str(2 * cache.operation_cost_upper_usd),
                "cache_operation_cost_basis_ref": cache.operation_cost_basis_ref,
            }
            cost += 2 * cache.operation_cost_upper_usd
        return Execution(
            "A",
            cost,
            1,
            input_tokens=1,
            output_tokens=1,
            actual_model=model.model,
            cache_observation=observation,
        )


def dispatch(value, data, payloads, *, fail=False, authority=None, record=None):
    reservations, receipts, cases = [], [], []
    backend = Backend(reservations, fail=fail)

    def reserve(amount, model, purpose):
        reservations.append((amount, model, purpose))
        return uuid4()

    report = run(
        value,
        data,
        backend,
        approval_hash=plan_summary(value, data)["plan_hash"],
        independent_labels=True,
        representative=True,
        source_policy_payloads=payloads,
        check_authority=authority or (lambda: None),
        reserve_call=reserve,
        record_call=record or (lambda *args: receipts.append(args)),
        record_case=cases.append,
    )
    return report, backend, reservations, receipts, cases


def test_policy_native_leaves_are_reserved_before_dispatch_with_cache_costs_and_safe_report():
    value, data, payloads = prepared()
    report, backend, reservations, receipts, cases = dispatch(value, data, payloads)
    assert report["status"] == "COMPLETED" and report["requests"] == 4
    assert len(backend.calls) == len(reservations) == len(receipts) == len(cases) == 4
    assert [model.model for model, _ in backend.calls] == [
        "gpt-4.1-mini",
        "gpt-4.1-mini",
        "gpt-4.1-mini",
        "gpt-4.1",
    ]
    assert all(item[2] == "evaluation" for item in reservations)
    assert report["models"][1]["exact_cache"]["cache_operations"] == 4
    assert report["models"][1]["exact_cache"]["execution_qualified"] is False
    assert report["source_policy_execution"]["application_hosting_cost_usd"] is None
    assert report["synthetic_execution"]["customer_savings_verified"] is False
    assert {item["model_key"] for item in cases} == {"reference", "routed-cache"}


def test_uncertain_native_request_is_not_replayed_or_followed_by_a_new_candidate():
    report, backend, reservations, receipts, cases = dispatch(*prepared(), fail=True)
    assert report["status"] == "FAILED" and len(backend.calls) == len(reservations) == 1
    assert report["code"] == "POLICY_NATIVE_CALL_UNCERTAIN_NO_FURTHER_REQUESTS"
    assert report["spent_modeled_usd"] is None and report["billing_complete"] is False
    assert len(receipts) == 1 and receipts[0][2] == "uncertain" and cases == []
    assert "OWNED_PRIVATE_EXCEPTION_CONTENT" not in str(report)


def test_lost_receipt_acknowledgement_stops_without_refunding_or_dispatching_again():
    def lost(*args):
        raise ConnectionError("owned receipt acknowledgement lost")

    report, backend, reservations, _, cases = dispatch(*prepared(), record=lost)
    assert report["status"] == "FAILED" and len(backend.calls) == 1
    assert Decimal(report["reserved_usd"]) == reservations[0][0]
    assert report["code"] == "CALL_RECEIPT_WRITE_UNCERTAIN_NO_REQUEST_RETRY" and cases == []


def test_current_authority_is_checked_again_before_every_native_or_cache_operation():
    checks = []

    def authority():
        checks.append(True)
        if len(checks) > 2:
            raise ValueError("POLICY_AUTHORITY_REVOKED")

    report, backend, reservations, _, cases = dispatch(*prepared(), authority=authority)
    assert report["status"] == "FAILED" and report["code"] == "POLICY_AUTHORITY_REVOKED"
    assert len(backend.calls) == len(reservations) == len(cases) == 1


def test_training_receipts_use_optimization_purpose_and_never_offer_quality_recommendations():
    report, backend, reservations, _, _ = dispatch(*prepared(training=True))
    assert report["status"] == "COMPLETED" and len(backend.calls) == 4
    assert all(item[2] == "optimization" for item in reservations)
    assert report["recommendations"] == []
    assert report["source_policy_execution"]["heldout_quality_claim"] is False


def test_changed_private_body_or_missing_durable_scope_is_rejected_before_execution():
    value, data, payloads = prepared()
    body = payloads[value.source_policy_execution.candidates[0].payload_id]
    body["policy"]["strategy"]["routes"][0]["target"]["instruction"] = "changed"
    with pytest.raises(ValueError, match="BINDING_OR_FRAME_CHANGED"):
        dispatch(value, data, payloads)
    with pytest.raises(ValueError, match="SCOPED_EXECUTION_ADAPTER_REQUIRED"):
        run(value, data, Backend([]), approval_hash=plan_summary(value, data)["plan_hash"])
