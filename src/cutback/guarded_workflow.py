"""Explicit JSON application boundary. No global SDK patch or automatic retries."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from cutback.core.episodes import bounded_episode_value
from cutback.core.guardrails import GuardrailBinding, GuardrailDecision
from cutback.core.tool_policy import canonical
from cutback.guardrails import strict_json


class SyncChecker(Protocol):
    @property
    def binding(self) -> GuardrailBinding: ...

    def check(self, *, operation_id, stage, content) -> GuardrailDecision: ...


class AsyncChecker(Protocol):
    @property
    def binding(self) -> GuardrailBinding: ...

    async def check(self, *, operation_id, stage, content) -> GuardrailDecision: ...


@dataclass(frozen=True)
class GuardedResult:
    value: object = field(repr=False)
    decisions: tuple[GuardrailDecision, ...]
    dispatched: bool = True

    @property
    def coverage_complete(self):
        return all(value.coverage_complete for value in self.decisions)


class GuardrailRejected(ValueError):
    """Metadata only. A denial after dispatch cannot undo calls or tool effects."""

    def __init__(self, code, decisions=(), *, dispatched=False):
        super().__init__(code)
        self.decisions = tuple(decisions)
        self.dispatched = dispatched
        # No price is inferred from whether a response could be delivered.
        self.cost = None


def _snapshot(value, binding, *, dispatched, decisions=()):
    try:
        bounded_episode_value(value, max_bytes=binding.max_content_bytes)
        return strict_json(canonical(value))
    except Exception:
        raise GuardrailRejected(
            "GUARDRAIL_JSON_BOUNDARY_REQUIRED", decisions, dispatched=dispatched
        ) from None


def _current(binding, decisions, *, dispatched):
    now = datetime.now(UTC)
    if not binding.starts_at <= now < binding.expires_at:
        raise GuardrailRejected("GUARDRAIL_AUTHORITY_NOT_CURRENT", decisions, dispatched=dispatched)


def _admit(decision, decisions, binding, *, dispatched, operation, stage):
    # Treat even an accidentally substituted checker conservatively. Actual HTTP
    # clients additionally authenticate service, principal and workflow receipts.
    if (
        decision.operation_id != operation
        or decision.stage != stage
        or decision.policy_id != binding.policy_id
        or decision.policy_sha256 != binding.policy_sha256
        or decision.checks_expected != binding.stage_rule_counts[decision.stage]
        or decision.action == "allow"
        and decision.status == "unavailable"
        and binding.on_unavailable != "fail_open"
    ):
        raise GuardrailRejected("GUARDRAIL_RECEIPT_SCOPE_INVALID", decisions, dispatched=dispatched)
    if decision.action != "allow":
        raise GuardrailRejected(decision.code, decisions, dispatched=dispatched)
    _current(binding, decisions, dispatched=dispatched)


def _scope(checker, input_stage, output_stage):
    binding = GuardrailBinding.model_validate_json(
        canonical(checker.binding.model_dump(mode="json"))
    )
    if not {input_stage, output_stage} <= set(binding.protected_stages):
        raise ValueError("GUARDRAIL_BOTH_INPUT_AND_OUTPUT_STAGES_REQUIRED")
    return binding


class GuardedWorkflow:
    """Call a reviewed function once, quarantine its entire bounded JSON result.

    The application performs any native SDK conversion inside that function. It
    must not deliver output itself. Streams and arbitrary native objects are not
    qualified here; return an explicit JSON application result instead.
    """

    def __init__(self, checker: SyncChecker, *, tool=False):
        self.input_stage, self.output_stage = (
            ("tool_input", "tool_output") if tool else ("input", "output")
        )
        self.checker = checker
        self.binding = _scope(checker, self.input_stage, self.output_stage)

    def invoke(self, value, function: Callable):
        value = _snapshot(value, self.binding, dispatched=False)
        operation = uuid4()
        before = self.checker.check(operation_id=operation, stage=self.input_stage, content=value)
        _admit(
            before,
            (before,),
            self.binding,
            dispatched=False,
            operation=operation,
            stage=self.input_stage,
        )
        try:
            result = function(value)
        except Exception:
            raise GuardrailRejected(
                "GUARDRAIL_APPLICATION_CALL_FAILED_NO_AUTOMATIC_RETRY", (before,), dispatched=True
            ) from None
        result = _snapshot(result, self.binding, dispatched=True, decisions=(before,))
        after = self.checker.check(operation_id=operation, stage=self.output_stage, content=result)
        _admit(
            after,
            (before, after),
            self.binding,
            dispatched=True,
            operation=operation,
            stage=self.output_stage,
        )
        return GuardedResult(result, (before, after))


class AsyncGuardedWorkflow:
    """Await one reviewed function. Cancellation propagates without replay."""

    def __init__(self, checker: AsyncChecker, *, tool=False):
        self.input_stage, self.output_stage = (
            ("tool_input", "tool_output") if tool else ("input", "output")
        )
        self.checker = checker
        self.binding = _scope(checker, self.input_stage, self.output_stage)

    async def invoke(self, value, function: Callable[..., Awaitable]):
        value = _snapshot(value, self.binding, dispatched=False)
        operation = uuid4()
        before = await self.checker.check(
            operation_id=operation, stage=self.input_stage, content=value
        )
        _admit(
            before,
            (before,),
            self.binding,
            dispatched=False,
            operation=operation,
            stage=self.input_stage,
        )
        try:
            result = await function(value)
        except Exception:
            raise GuardrailRejected(
                "GUARDRAIL_APPLICATION_CALL_FAILED_NO_AUTOMATIC_RETRY", (before,), dispatched=True
            ) from None
        result = _snapshot(result, self.binding, dispatched=True, decisions=(before,))
        after = await self.checker.check(
            operation_id=operation, stage=self.output_stage, content=result
        )
        _admit(
            after,
            (before, after),
            self.binding,
            dispatched=True,
            operation=operation,
            stage=self.output_stage,
        )
        return GuardedResult(result, (before, after))
