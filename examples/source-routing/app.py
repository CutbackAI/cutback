import json
from pathlib import Path

from cutback.evals.experiments import ModelSpec, qualified_native_model, route_target

ROOT = Path(__file__).resolve().parent


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ROUTING_SOURCE_DUPLICATE_CONFIGURATION_KEY")
        result[key] = value
    return result


def classify(client, text):
    from importlib.metadata import version

    from openai import OpenAI

    if type(text) is not str or len(text) > 95904:
        raise ValueError("ROUTING_SOURCE_REQUIRES_BOUNDED_TEXT_INPUT")
    if type(client) is not OpenAI or version("openai") not in {"2.29.0", "3.35.1"}:
        raise ValueError("ROUTING_SOURCE_REQUIRES_QUALIFIED_SYNC_OPENAI_CLIENT")
    if str(client.base_url) != "https://api.openai.com/v1/":
        raise ValueError("ROUTING_SOURCE_REQUIRES_OFFICIAL_NATIVE_ENDPOINT")
    try:
        with (ROOT / "routing.json").open("rb") as source:
            raw = source.read(262145)
        if len(raw) > 262144:
            raise ValueError
        policy = ModelSpec.model_validate(
            json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
        )
    except (OSError, ValueError, RecursionError):
        raise ValueError("ROUTING_SOURCE_CONFIGURATION_UNAVAILABLE_OR_INVALID") from None
    target = route_target(policy, text)
    if (target.provider != "openai" or target.request_contract or target.native_contract
            or target.prompt_cache or target.agent_turn):
        raise ValueError("ROUTING_SOURCE_TARGET_CONTRACT_UNSUPPORTED")
    qualified_native_model(target)
    return client.responses.create(
        model=target.model,
        input=text,
        instructions=target.instruction,
        max_output_tokens=target.max_output_tokens,
        store=False,
    )
