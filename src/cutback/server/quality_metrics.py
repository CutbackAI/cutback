"""Explicitly limited judge interpretation; output contracts are shared with agent fixtures."""

from cutback.evals.output_contracts import QualityMetricLimit, bounded_json
from cutback.evals.output_contracts import output_contract as output_contract


def judge_verdict(text, captured_input, captured_output):
    try:
        value = bounded_json(text)
    except QualityMetricLimit:
        return "error", "JUDGE_RESPONSE_JSON_INVALID"
    if (
        not isinstance(value, dict)
        or set(value) != {"outcome", "evidence_quote"}
        or not isinstance(value["outcome"], str)
        or value["outcome"] not in {"passed", "failed", "unknown"}
        or not isinstance(value["evidence_quote"], str)
    ):
        return "error", "JUDGE_RESPONSE_CONTRACT_INVALID"
    outcome, quote = value["outcome"], value["evidence_quote"]
    if outcome != "unknown" and (
        not quote
        or not any(
            quote in content
            for content in (captured_input, captured_output)
            if isinstance(content, str)
        )
    ):
        return "error", "JUDGE_QUOTE_NOT_IN_CAPTURED_CONTENT"
    return outcome, "MODEL_JUDGE_NOT_INDEPENDENT_GOLD"


def input_bucket(value):
    if not isinstance(value, str):
        return None
    length = len(value.encode())
    for bound, key in (
        (0, "empty"),
        (127, "bytes_1_127"),
        (511, "bytes_128_511"),
        (2047, "bytes_512_2047"),
        (8191, "bytes_2048_8191"),
        (32767, "bytes_8192_32767"),
    ):
        if length <= bound:
            return key
    return "bytes_32768_plus"
