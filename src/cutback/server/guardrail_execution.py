"""Host-side durable guard checks; the checker process receives no database key."""

import asyncio

from cutback.core.guardrails import GuardrailBinding, GuardrailDecision
from cutback.server.guardrail_controls import finish_check, reserve_check


class DurableGuardrailChecker:
    def __init__(self, sessions, client):
        self.sessions, self.client = sessions, client
        self._binding_json = client.binding.model_dump_json()

    @property
    def binding(self):
        return GuardrailBinding.model_validate_json(self._binding_json)

    def _denied(self, operation, stage):
        binding = self.binding
        return GuardrailDecision(
            operation_id=operation,
            policy_id=binding.policy_id,
            policy_sha256=binding.policy_sha256,
            stage=stage,
            status="blocked",
            action="deny",
            code="GUARDRAIL_SCOPE_DENIED",
            checks_completed=0,
            checks_expected=binding.stage_rule_counts.get(stage, 0),
            coverage_complete=False,
        )

    async def check(self, *, operation_id, stage, content):
        binding = self.binding

        def begin():
            with self.sessions.begin() as session:
                reserve_check(
                    session, binding.project_id, binding.policy_id, binding, operation_id, stage
                )

        def complete(decision):
            with self.sessions.begin() as session:
                return finish_check(
                    session, binding.project_id, binding.policy_id, binding, decision
                )

        try:
            # Cancelled/lost intent acknowledgements cannot authorize redispatch.
            await asyncio.to_thread(begin)
            decision = await self.client.check(
                operation_id=operation_id, stage=stage, content=content
            )
            return await asyncio.to_thread(complete, decision)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A configured fail-open mode cannot override current authority,
            # shared quotas, durable intent identity or missing database evidence.
            return self._denied(operation_id, stage)
