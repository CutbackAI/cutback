import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from unit.test_guardrail_client import binding, reply
from unit.test_guardrails import policy

from cutback.guarded_workflow import AsyncGuardedWorkflow, GuardedWorkflow, GuardrailRejected
from cutback.guardrail_client import AsyncGuardrailClient, GuardrailClient


def workflow(definition=None, *, unavailable=False, async_mode=False):
    definition, scope = binding(definition)

    def dispatch(request):
        if unavailable:
            raise httpx.ReadError("PRIVATE_TRANSPORT_DETAIL")
        return httpx.Response(200, json=reply(definition, scope, request))

    client_type = AsyncGuardrailClient if async_mode else GuardrailClient
    client = client_type(
        "https://owned-guard.invalid",
        "x" * 48,
        binding=scope,
        workflow="owned-record-task",
        transport=httpx.MockTransport(dispatch),
    )
    guarded_type = AsyncGuardedWorkflow if async_mode else GuardedWorkflow
    return guarded_type(client), client


def test_input_denial_never_dispatches_output_denial_never_delivers_and_cost_stays_unknown():
    guarded, client = workflow()
    calls = []

    def invoke(value):
        calls.append(value)
        return {"answer": "SYNTHETIC_DENIED_LITERAL_OWNED"}

    try:
        with pytest.raises(GuardrailRejected) as denied:
            guarded.invoke("SYNTHETIC_DENIED_LITERAL_OWNED", invoke)
        assert calls == [] and not denied.value.dispatched
        with pytest.raises(GuardrailRejected) as quarantined:
            guarded.invoke("benign", invoke)
        assert calls == ["benign"] and quarantined.value.dispatched
        assert quarantined.value.cost is None
        assert len(quarantined.value.decisions) == 2
        assert "SYNTHETIC_DENIED" not in repr(quarantined.value)
        assert "answer" not in vars(quarantined.value)
    finally:
        client.close()


def test_same_json_snapshot_is_checked_and_delivered_without_mutable_aliases():
    guarded, client = workflow()
    original = {"ticket": ["benign"]}
    output = {"answer": ["safe"]}

    def invoke(value):
        assert value == original and value is not original
        value["ticket"].append("function-owned")
        return output

    try:
        result = guarded.invoke(original, invoke)
        output["answer"].append("SYNTHETIC_DENIED_LITERAL_OWNED")
        assert result.value == {"answer": ["safe"]}
        assert original == {"ticket": ["benign"]}
        assert result.coverage_complete
        assert "safe" not in repr(result)
    finally:
        client.close()


@pytest.mark.parametrize("fail_mode", ["fail_closed", "fail_open"])
def test_outage_follows_exact_approved_mode_and_never_claims_known_coverage(fail_mode):
    guarded, client = workflow(policy(on_unavailable=fail_mode), unavailable=True)
    calls = []
    try:
        if fail_mode == "fail_closed":
            with pytest.raises(GuardrailRejected):
                guarded.invoke("benign", lambda value: calls.append(value))
            assert calls == []
        else:
            result = guarded.invoke("benign", lambda value: calls.append(value) or "safe")
            assert calls == ["benign"] and not result.coverage_complete
            assert all(value.status == "unavailable" for value in result.decisions)
    finally:
        client.close()


def test_application_failure_is_not_retried_or_exposed_and_output_type_is_explicit():
    guarded, client = workflow()
    calls = []

    def failed(value):
        calls.append(value)
        raise RuntimeError("PRIVATE_APPLICATION_EXCEPTION")

    try:
        with pytest.raises(GuardrailRejected) as failure:
            guarded.invoke("benign", failed)
        assert calls == ["benign"] and failure.value.dispatched
        assert "PRIVATE_APPLICATION" not in str(failure.value)
        with pytest.raises(GuardrailRejected) as unsupported:
            guarded.invoke("benign", lambda _: iter(["partial-stream"]))
        assert unsupported.value.dispatched
        with pytest.raises(GuardrailRejected) as invalid:
            guarded.invoke({"nonfinite": float("nan")}, failed)
        assert not invalid.value.dispatched and calls == ["benign"]
    finally:
        client.close()


def test_expiry_during_application_call_blocks_delivery_after_actual_dispatch():
    guarded, client = workflow()

    def invoke(_):
        guarded.binding.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        return "safe"

    try:
        with pytest.raises(GuardrailRejected) as expired:
            guarded.invoke("benign", invoke)
        assert expired.value.dispatched and expired.value.cost is None
        assert str(expired.value) == "GUARDRAIL_AUTHORITY_NOT_CURRENT"
    finally:
        client.close()


def test_mismatched_operation_receipt_cannot_authorize_dispatch():
    guarded, client = workflow()
    original = client.check

    def wrong(**values):
        result = original(**values)
        result.operation_id = uuid4()
        return result

    client.check = wrong
    try:
        with pytest.raises(GuardrailRejected) as mismatch:
            guarded.invoke("benign", lambda _: pytest.fail("must not dispatch"))
        assert str(mismatch.value) == "GUARDRAIL_RECEIPT_SCOPE_INVALID"
        assert not mismatch.value.dispatched
    finally:
        client.close()


def test_tool_policy_only_narrows_invocation_output_denial_cannot_undo_state_effect():
    definition = policy(
        protected_stages=["tool_input", "tool_output"],
        rules=[
            {"kind": "tool_allowlist", "key": "scoped-tools", "names": ["read_owned"]},
            {
                "kind": "literal_deny",
                "key": "output-rule",
                "stages": ["tool_output"],
                "terms": ["SYNTHETIC_DENIED_LITERAL_OWNED"],
            },
        ],
    )
    definition, scope = binding(definition)
    client = GuardrailClient(
        "https://owned-guard.invalid",
        "x" * 48,
        binding=scope,
        workflow="owned-record-task",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json=reply(definition, scope, req))
        ),
    )
    guarded, effects = GuardedWorkflow(client, tool=True), []
    try:
        with pytest.raises(GuardrailRejected):
            guarded.invoke({"name": "delete_all", "arguments": {}}, lambda _: effects.append("bad"))
        assert effects == []

        def invoke(_):
            effects.append("already-completed")
            return "SYNTHETIC_DENIED_LITERAL_OWNED"

        with pytest.raises(GuardrailRejected) as blocked:
            guarded.invoke({"name": "read_owned", "arguments": {}}, invoke)
        assert effects == ["already-completed"] and blocked.value.dispatched
    finally:
        client.close()


def test_async_allow_deny_and_cancellation_propagate_without_second_dispatch():
    async def run():
        guarded, client = workflow(async_mode=True)
        calls = []

        async def invoke(value):
            calls.append(value)
            return "safe"

        async def cancelled(value):
            calls.append(value)
            raise asyncio.CancelledError

        try:
            result = await guarded.invoke("benign", invoke)
            assert result.value == "safe" and result.coverage_complete
            with pytest.raises(GuardrailRejected):
                await guarded.invoke("SYNTHETIC_DENIED_LITERAL_OWNED", invoke)
            with pytest.raises(asyncio.CancelledError):
                await guarded.invoke("cancelled", cancelled)
            assert calls == ["benign", "cancelled"]
        finally:
            await client.close()

    asyncio.run(run())
