"""Explicit local enforcement. No global SDK patch, implicit export or model calls.

Literal and bounded structural rules establish only those declared checks. They
are not a semantic safety classifier or a replacement for tool authorization.
"""

import hashlib
import json
import time
import unicodedata
from copy import deepcopy
from datetime import UTC, datetime, timedelta

from cutback.core.episodes import bounded_episode_value
from cutback.core.guardrails import (
    GuardrailDecision,
    GuardrailPolicy,
    JSONShapeRule,
    LiteralDenyRule,
    ToolAllowlistRule,
)
from cutback.core.tool_policy import canonical


def strict_json(value):
    def pairs(items):
        result = {}
        for key, child in items:
            if key in result:
                raise ValueError("GUARDRAIL_DUPLICATE_JSON_KEY")
            result[key] = child
        return result

    def nonfinite(_):
        raise ValueError("GUARDRAIL_NONFINITE_JSON")

    return json.loads(value, object_pairs_hook=pairs, parse_constant=nonfinite)


def string_values(value):
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            yield item
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


class GuardrailConfigurationError(ValueError):
    def __init__(self):
        super().__init__("GUARDRAIL_POLICY_VALIDATOR_CONFIGURATION_INVALID")


class GuardrailEngine:
    """Bounded in-process checks with an immutable private policy snapshot.

    Evaluation time limits are cooperative checks around bounded built-in work,
    not an OS sandbox for arbitrary validators. No plugin code is loaded.
    """

    def __init__(self, policy: GuardrailPolicy):
        self._snapshot = canonical(policy.model_dump(mode="json"))
        self._metadata = deepcopy(policy.public_metadata())
        self._closed = False
        self._validators = {}
        captured = GuardrailPolicy.model_validate_json(self._snapshot)
        for rule in captured.rules:
            if isinstance(rule, JSONShapeRule):
                try:
                    from jsonschema import Draft202012Validator

                    Draft202012Validator.check_schema(rule.schema_definition)
                    self._validators[rule.key] = Draft202012Validator(rule.schema_definition)
                except Exception:
                    self.close()
                    raise GuardrailConfigurationError() from None

    def public_metadata(self):
        return deepcopy(self._metadata)

    def close(self):
        self._closed = True
        self._snapshot = b""
        self._validators.clear()

    def check(
        self,
        *,
        operation_id,
        stage,
        content,
        project_id,
        principal_id,
        environment,
        workflow,
        now=None,
    ):
        started = time.monotonic()
        instant = now or datetime.now(UTC)
        policy = None if self._closed else GuardrailPolicy.model_validate_json(self._snapshot)
        rules = [] if policy is None else [rule for rule in policy.rules if stage in rule.stages]
        completed = 0

        def result(status, action, code, *, coverage=False, refs=()):
            return GuardrailDecision(
                operation_id=operation_id,
                policy_id=self._metadata["policy_id"],
                policy_sha256=self._metadata["policy_sha256"],
                stage=stage,
                status=status,
                action=action,
                code=code,
                rule_refs=refs,
                checks_completed=completed,
                checks_expected=len(rules),
                coverage_complete=coverage,
                duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            )

        if policy is None:
            return result("blocked", "deny", "GUARDRAIL_SCOPE_DENIED")
        # Authority and resource limits never inherit detector fail-open behavior.
        if (
            (project_id, principal_id, environment)
            != (policy.project_id, policy.principal_id, policy.environment)
            or workflow not in policy.workflows
            or stage not in policy.protected_stages
            or instant.tzinfo is None
            or instant < policy.starts_at
        ):
            return result("blocked", "deny", "GUARDRAIL_SCOPE_DENIED")
        if instant >= policy.expires_at:
            return result("blocked", "deny", "GUARDRAIL_EXPIRED")
        try:
            bounded_episode_value(content, max_bytes=policy.max_content_bytes)
        except (ValueError, TypeError, RecursionError, UnicodeError, OverflowError):
            return result("blocked", "deny", "GUARDRAIL_CONTENT_BOUND")

        def timely():
            return (time.monotonic() - started) * 1000 <= policy.max_evaluation_ms

        def unavailable():
            return result(
                "unavailable",
                "allow" if policy.on_unavailable == "fail_open" else "deny",
                "GUARDRAIL_CHECK_UNAVAILABLE",
            )

        for rule in rules:
            if not timely():
                return unavailable()
            code = None
            try:
                if isinstance(rule, LiteralDenyRule):
                    normalize = (
                        (lambda value: unicodedata.normalize("NFKC", value).casefold())
                        if rule.normalization == "nfkc_casefold"
                        else (lambda value: value)
                    )
                    terms = tuple(normalize(term) for term in rule.terms)
                    for text in string_values(content):
                        normalized = normalize(text)
                        if any(term in normalized for term in terms):
                            code = "GUARDRAIL_LITERAL_DENIED"
                            break
                        if not timely():
                            return unavailable()
                elif isinstance(rule, JSONShapeRule):
                    try:
                        value = strict_json(content) if isinstance(content, str) else content
                    except (ValueError, TypeError, RecursionError):
                        code = "GUARDRAIL_SHAPE_DENIED"
                    else:
                        validator = self._validators[rule.key]
                        if next(validator.iter_errors(value), None) is not None:
                            code = "GUARDRAIL_SHAPE_DENIED"
                elif isinstance(rule, ToolAllowlistRule):
                    if (
                        type(content) is not dict
                        or set(content) != {"name", "arguments"}
                        or content.get("name") not in rule.names
                        or type(content.get("arguments")) is not dict
                    ):
                        code = "GUARDRAIL_TOOL_DENIED"
                completed += 1
            except Exception:
                return unavailable()
            if code is not None:
                reference = hashlib.sha256(
                    canonical([str(policy.content_nonce), rule.key])
                ).hexdigest()
                return result("blocked", "deny", code, refs=(reference,))
            if not timely():
                return unavailable()
        if instant + timedelta(seconds=time.monotonic() - started) >= policy.expires_at:
            return result("blocked", "deny", "GUARDRAIL_EXPIRED")
        return result("allowed", "allow", "GUARDRAIL_ALLOWED", coverage=True)
