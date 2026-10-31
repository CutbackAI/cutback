"""One-shot reactive user turns over observed evidence, using the shared call ledger."""

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from cutback.core.episodes import bounded_episode_value
from cutback.core.request_profiles import RequestContract
from cutback.core.user_simulation import (
    USER_SIMULATION_INSTRUCTION,
    USER_SIMULATION_SCHEMA,
    UserSimulationReceipt,
    UserSimulationResponse,
)


def user_simulation_model(model):
    from cutback.evals.experiments import ModelSpec

    if (
        model.provider != "openai"
        or model.agent_turn
        or model.routes
        or model.prompt_cache
        or model.native_contract
    ):
        raise ValueError("REACTIVE_USER_REQUIRES_QUALIFIED_OPENAI_RESPONSES_TEXT")
    value = model.model_dump(mode="json")
    value["instruction"] = USER_SIMULATION_INSTRUCTION
    value["request_contract"] = RequestContract(
        sdk_surface="responses.create",
        parameters=["input"],
        payload={"op": "parameter", "name": "input"},
        options={
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "cutback_reactive_user",
                    "strict": True,
                    "schema": USER_SIMULATION_SCHEMA,
                }
            }
        },
    ).model_dump(mode="json")
    return ModelSpec.model_validate(value)


def simulation_document(specification, events):
    # No scenario object, gold, references, unreturned fixtures, system prompt,
    # hidden state or assertions can enter this projection.
    conversation = []
    for event in events:
        if event.kind in {"user", "assistant"}:
            conversation.append({"role": event.kind, "text": event.text})
        elif event.kind == "tool":
            conversation.append(
                {
                    "role": "tool",
                    "tool": event.tool,
                    "status": event.status,
                    "result": event.result,
                }
            )
    value = {"persona": specification.persona, "observed_conversation": conversation}
    bounded_episode_value(value, max_bytes=specification.max_transcript_bytes)
    return json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False)


@dataclass
class SimulationState:
    requests: int = 0
    generated_turns: int = 0
    known_cost_requests: int = 0
    spent: Decimal = Decimal(0)
    status: Literal["ended", "turn_cap", "target_stopped", "incomplete"] = "incomplete"

    def receipt(self):
        return UserSimulationReceipt(
            status=self.status,
            requests=self.requests,
            generated_turns=self.generated_turns,
            known_cost_requests=self.known_cost_requests,
            known_cost_subtotal_usd=self.spent,
        )

    def next_turn(self, spec, events, ledger, executor, check_authority):
        from cutback.evals.agent_executor import strict_json

        if self.generated_turns >= spec.max_turns:
            self.status = "turn_cap"
            return None
        document = simulation_document(spec, events)
        if check_authority:
            check_authority()
        before = ledger.requests, ledger.known_cost_requests, ledger.spent
        try:
            result = ledger.call(executor, spec.model, document, traffic_kind="simulation")
        finally:
            self.requests += ledger.requests - before[0]
            self.known_cost_requests += ledger.known_cost_requests - before[1]
            self.spent += ledger.spent - before[2]
        if check_authority:
            check_authority()
        if not result.complete or result.cost_usd is None:
            raise ValueError("REACTIVE_USER_INCOMPLETE_OR_UNKNOWN_USAGE")
        response = UserSimulationResponse.model_validate(strict_json(result.output))
        if response.action == "end":
            self.status = "ended"
            return None
        self.generated_turns += 1
        return response.message
