"""Known allow/deny/unknown outcomes; no classifier or paid provider claims."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from cutback.core.guardrails import GuardrailDecision, GuardrailPolicy
from cutback.guardrails import GuardrailConfigurationError, GuardrailEngine


def policy(**changes):
    now = datetime.now(UTC)
    return GuardrailPolicy.model_validate(
        {
            "policy_id": str(uuid4()),
            "project_id": str(uuid4()),
            "principal_id": str(uuid4()),
            "environment": "staging",
            "workflows": ["owned-record-task"],
            "starts_at": (now - timedelta(minutes=1)).isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat(),
            "protected_stages": ["input", "output"],
            "content_use_approved": True,
            "rules": [
                {
                    "kind": "literal_deny",
                    "key": "private-owner-rule",
                    "stages": ["input", "output"],
                    "terms": ["SYNTHETIC_DENIED_LITERAL_OWNED"],
                    "normalization": "nfkc_casefold",
                }
            ],
            **changes,
        }
    )


def check(engine, definition, content, *, stage="input", **scope):
    return engine.check(
        operation_id=uuid4(),
        stage=stage,
        content=content,
        project_id=definition.project_id,
        principal_id=definition.principal_id,
        environment=definition.environment,
        workflow="owned-record-task",
        **scope,
    )


def test_private_snapshot_cannot_be_changed_by_mutating_the_callers_rules():
    definition = policy()
    engine = GuardrailEngine(definition)
    original_hash = engine.public_metadata()["policy_sha256"]
    definition.rules[0].terms = ("CHANGED_AFTER_SNAPSHOT",)
    assert check(engine, definition, "SYNTHETIC_DENIED_LITERAL_OWNED").status == "blocked"
    assert check(engine, definition, "CHANGED_AFTER_SNAPSHOT").status == "allowed"
    assert engine.public_metadata()["policy_sha256"] == original_hash
    public = json.dumps(engine.public_metadata())
    assert "SYNTHETIC_DENIED_LITERAL_OWNED" not in public
    assert "private-owner-rule" not in public and "content_nonce" not in public
    assert str(definition.content_nonce) not in public
    exported = engine.public_metadata()
    exported["workflows"].clear()
    assert engine.public_metadata()["workflows"] == ["owned-record-task"]
    denied = check(engine, definition, {"reply": ["synthetic_denied_literal_owned"]})
    assert denied.action == "deny" and denied.code == "GUARDRAIL_LITERAL_DENIED"
    assert len(denied.rule_refs) == 1 and "private-owner-rule" not in denied.model_dump_json()
    engine.close()
    assert check(engine, definition, "owned").action == "deny"
    assert check(engine, definition, "owned").status == "blocked"
    assert engine._snapshot == b"" and not engine._validators


@pytest.mark.parametrize("mode", ["fail_open", "fail_closed"])
def test_expired_scope_and_resource_limits_never_inherit_detector_fail_open(mode):
    definition = policy(on_unavailable=mode, max_content_bytes=256)
    engine = GuardrailEngine(definition)
    assert check(engine, definition, "x" * 257).code == "GUARDRAIL_CONTENT_BOUND"
    for changes in (
        {"project_id": uuid4()},
        {"principal_id": uuid4()},
        {"environment": "production"},
        {"workflow": "other-task"},
    ):
        scope = dict(
            project_id=definition.project_id,
            principal_id=definition.principal_id,
            environment=definition.environment,
            workflow="owned-record-task",
        )
        scope.update(changes)
        result = engine.check(operation_id=uuid4(), stage="input", content="owned", **scope)
        assert result.action == "deny" and result.code == "GUARDRAIL_SCOPE_DENIED"
    result = check(engine, definition, "owned", now=definition.expires_at)
    assert result.action == "deny" and result.code == "GUARDRAIL_EXPIRED"
    assert check(engine, definition, "owned", stage="tool_output").action == "deny"


@pytest.mark.parametrize("mode,action", [("fail_closed", "deny"), ("fail_open", "allow")])
def test_detector_time_budget_keeps_fail_open_unknown_not_passed(monkeypatch, mode, action):
    definition = policy(on_unavailable=mode)
    engine = GuardrailEngine(definition)
    times = iter((0.0, 0.060, 0.070))
    monkeypatch.setattr("cutback.guardrails.time.monotonic", lambda: next(times))
    result = check(engine, definition, "owned")
    assert result.action == action and result.status == "unavailable"
    assert not result.coverage_complete and result.checks_completed == 0
    assert result.checks_expected == 1


def test_structural_checks_do_not_resolve_references_execute_regex_or_accept_duplicate_json():
    for schema in (
        {"$ref": "https://external.invalid/private"},
        {"type": "string", "pattern": "(a+)+$"},
    ):
        with pytest.raises(ValueError, match="BOUNDED_SUBSET"):
            policy(
                rules=[
                    {
                        "kind": "json_shape",
                        "key": "owned-shape",
                        "stages": ["input", "output"],
                        "schema_definition": schema,
                    }
                ]
            )
    invalid = policy(
        rules=[
            {
                "kind": "json_shape",
                "key": "owned-shape",
                "stages": ["input", "output"],
                "schema_definition": {"type": "not-a-json-type"},
            }
        ]
    )
    with pytest.raises(GuardrailConfigurationError) as failure:
        GuardrailEngine(invalid)
    assert "not-a-json-type" not in str(failure.value)
    definition = policy(
        rules=[
            {
                "kind": "json_shape",
                "key": "owned-shape",
                "stages": ["input", "output"],
                "schema_definition": {
                    "type": "object",
                    "required": ["answer"],
                    "properties": {"answer": {"type": "string", "maxLength": 16}},
                    "additionalProperties": False,
                },
            }
        ]
    )
    engine = GuardrailEngine(definition)
    assert check(engine, definition, '{"answer":"resolved"}').status == "allowed"
    for text in (
        '{"answer":"first","answer":"second"}',
        '{"answer":true}',
        '{"answer":"resolved","extra":"hidden"}',
        '{"answer":NaN}',
        "not JSON",
    ):
        result = check(engine, definition, text)
        assert result.status == "blocked" and result.code == "GUARDRAIL_SHAPE_DENIED"


@pytest.mark.parametrize("mode,action", [("fail_closed", "deny"), ("fail_open", "allow")])
def test_validator_failure_does_not_echo_private_details_or_become_a_known_allow(mode, action):
    definition = policy(
        on_unavailable=mode,
        rules=[
            {
                "kind": "json_shape",
                "key": "owned-shape",
                "stages": ["input", "output"],
                "schema_definition": {"type": "object"},
            }
        ],
    )
    engine = GuardrailEngine(definition)

    class FailedValidator:
        def iter_errors(self, value):
            raise RuntimeError("SYNTHETIC_SECRET_VALIDATOR_DETAIL")

    engine._validators["owned-shape"] = FailedValidator()
    result = check(engine, definition, {})
    assert result.status == "unavailable" and result.action == action
    assert not result.coverage_complete and "SYNTHETIC_SECRET" not in result.model_dump_json()


def test_tool_envelopes_have_an_allowlist_but_do_not_grant_execution_privileges():
    definition = policy(
        protected_stages=["tool_input"],
        rules=[{"kind": "tool_allowlist", "key": "approved-tools", "names": ["records_read"]}],
    )
    engine = GuardrailEngine(definition)
    assert (
        check(
            engine,
            definition,
            {"name": "records_read", "arguments": {"resource_id": "owned"}},
            stage="tool_input",
        ).status
        == "allowed"
    )
    for content in (
        {"name": "shell", "arguments": {}},
        {"name": "records_read", "arguments": {}, "project_id": "spoof"},
        {"name": "records_read", "arguments": "not-an-object"},
    ):
        result = check(engine, definition, content, stage="tool_input")
        assert result.code == "GUARDRAIL_TOOL_DENIED" and result.action == "deny"


def test_actual_denial_cannot_be_relabelled_fail_open_or_incomplete_checks_as_allowed():
    definition = policy()
    denied = check(GuardrailEngine(definition), definition, "SYNTHETIC_DENIED_LITERAL_OWNED")
    raw = denied.model_dump(mode="json")
    with pytest.raises(ValueError, match="CANNOT_FAIL_OPEN"):
        GuardrailDecision.model_validate({**raw, "action": "allow"})
    raw.update(
        status="allowed",
        action="allow",
        code="GUARDRAIL_ALLOWED",
        rule_refs=[],
        checks_completed=0,
        coverage_complete=True,
    )
    with pytest.raises(ValueError, match="COMPLETE_KNOWN_CHECKS"):
        GuardrailDecision.model_validate(raw)
