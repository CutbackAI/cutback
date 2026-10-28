"""Bounded deterministic output contracts and explicitly limited judge interpretation."""

import json
import math


class QualityMetricLimit(ValueError):
    pass


def bounded_json(value):
    try:
        size = len(value.encode()) if isinstance(value, str) else 256001
    except UnicodeError:
        raise QualityMetricLimit("QUALITY_OUTPUT_JSON_INVALID") from None
    if size > 256000:
        raise QualityMetricLimit("QUALITY_OUTPUT_BYTES_EXCEEDED")

    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError("DUPLICATE_JSON_KEY")
            result[key] = item
        return result

    def finite(value):
        number = float(value)
        if not math.isfinite(number):
            raise QualityMetricLimit("QUALITY_OUTPUT_NUMBER_LIMIT")
        return number

    def integer(value):
        if len(value) > 1000:
            raise QualityMetricLimit("QUALITY_OUTPUT_NUMBER_LIMIT")
        return int(value)

    def constant(value):
        raise ValueError("NON_JSON_CONSTANT")

    try:
        parsed = json.loads(
            value,
            object_pairs_hook=pairs,
            parse_float=finite,
            parse_int=integer,
            parse_constant=constant,
        )
    except QualityMetricLimit:
        raise
    except (ValueError, RecursionError):
        raise QualityMetricLimit("QUALITY_OUTPUT_JSON_INVALID") from None
    stack = [(parsed, 0)]
    count = 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if count > 10000 or depth > 16:
            raise QualityMetricLimit("QUALITY_OUTPUT_STRUCTURE_LIMIT")
        if isinstance(item, dict):
            if len(item) > 256:
                raise QualityMetricLimit("QUALITY_OUTPUT_STRUCTURE_LIMIT")
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            if len(item) > 1000:
                raise QualityMetricLimit("QUALITY_OUTPUT_STRUCTURE_LIMIT")
            stack.extend((v, depth + 1) for v in item)
    return parsed


def output_contract(output, schema=None):
    if not isinstance(output, str):
        return "unknown", "OUTPUT_NOT_CAPTURED"
    if schema is None:
        return ("passed" if output.strip() else "failed"), "NONEMPTY_OUTPUT_CONTRACT"
    try:
        parsed = bounded_json(output)
    except QualityMetricLimit as exc:
        return (
            ("failed", "OUTPUT_JSON_CONTRACT_FAILED")
            if str(exc) == "QUALITY_OUTPUT_JSON_INVALID"
            else ("error", "OUTPUT_JSON_WORK_LIMIT")
        )
    from cutback.evals.metrics import MetricSpec

    try:
        MetricSpec(kind="json_schema", minimum=1, schema_definition=schema)
    except ValueError:
        return "error", "OUTPUT_SCHEMA_PROFILE_INVALID"
    from jsonschema import Draft202012Validator, validators

    budget = [100000]

    def wrap(fn):
        def bounded(validator, constraint, instance, definition):
            budget[0] -= 1
            if budget[0] < 0:
                raise QualityMetricLimit("SCHEMA_VALIDATION_WORK_LIMIT")
            yield from fn(validator, constraint, instance, definition)

        return bounded

    validator = validators.extend(
        Draft202012Validator,
        {name: wrap(fn) for name, fn in Draft202012Validator.VALIDATORS.items()},
    )
    try:
        passed = not any(validator(schema).iter_errors(parsed))
    except (ValueError, RecursionError):
        return "error", "SCHEMA_VALIDATION_WORK_LIMIT"
    return ("passed" if passed else "failed"), "PINNED_OUTPUT_SCHEMA_CONTRACT"


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
