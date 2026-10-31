"""Parse-only request eligibility for the bounded exact-cache experiment adapter."""

import json


def qualify_cache_inputs(model, inputs):
    if model.exact_cache is None:
        raise ValueError("EXACT_CACHE_EXPLICIT_CANDIDATE_REQUIRED")
    contract = model.request_contract
    if (
        model.provider != "openai"
        or contract is not None
        and (
            contract.sdk_surface != "responses.create"
            or set(contract.options) - {"temperature", "top_p"}
        )
    ):
        raise ValueError("EXACT_CACHE_STATELESS_TEXT_CONTRACT_REQUIRED")
    for text in inputs:
        try:
            rendered = contract.render(text) if contract is not None else text
            bundle = contract is not None and contract.version == "native-prompt-v1"
            value = rendered["input"] if bundle else rendered
            instruction = rendered.get("instructions") if bundle else model.instruction or None
            if (
                not isinstance(value, str)
                or instruction is not None
                and not isinstance(instruction, str)
            ):
                raise ValueError
            # Leave room for the pinned worker's provider headers and bindings.
            if (
                len(
                    json.dumps(
                        {"input": value, "instructions": instruction},
                        ensure_ascii=False,
                        allow_nan=False,
                    ).encode()
                )
                > 60000
            ):
                raise ValueError
        except (ValueError, TypeError, KeyError, RecursionError):
            raise ValueError("EXACT_CACHE_REQUEST_REQUIRES_BOUNDED_PLAIN_TEXT") from None
