"""Independent ticket triage; the caller owns the native client and reviewed policy."""

import json
from pathlib import Path

from cutback.evals.experiments import ModelSpec, route_target


def read_policy(path):
    policy = ModelSpec.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
    if policy.provider != "router" or any(row.target.provider != "openai" for row in policy.routes):
        raise ValueError("EXAMPLE_REQUIRES_REVIEWED_OPENAI_ROUTING_POLICY")
    return policy


def classify(client, text, policy):
    """Return the native response unchanged; never fall back or retry a rejected route."""
    target = route_target(policy, text)
    if target.provider != "openai" or target.request_contract or target.native_contract:
        raise ValueError("EXAMPLE_TARGET_CONTRACT_UNSUPPORTED")
    if target.prompt_cache:
        raise ValueError("EXAMPLE_PROMPT_CACHE_UNSUPPORTED")
    return client.responses.create(
        model=target.model,
        input=text,
        instructions=target.instruction,
        max_output_tokens=target.max_output_tokens,
        store=False,
    )


if __name__ == "__main__":
    # Offline preview only. Running this module constructs no SDK client.
    policy = read_policy(Path(__file__).with_name("routing.json"))
    for text in ["Priority: urgent. Ticket: AA.", "Priority: normal. " + "details " * 30]:
        target = route_target(policy, text)
        print(json.dumps({"input_bytes": len(text.encode("utf-8")), "model": target.model}))
