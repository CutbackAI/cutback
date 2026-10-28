"""Versioned token partitions. Prices are declared facts, never inferred invoice totals."""

from decimal import Decimal


def inclusive_write_breakdown(event, rate):
    counts = (
        event.input_tokens,
        event.output_tokens,
        event.cache_read_tokens,
        event.cache_write_tokens,
    )
    if any(value is None for value in counts):
        return "modern_cache_write_usage_unavailable"
    if any(type(value) is not int or value < 0 for value in counts):
        return "modern_usage_partition_invalid"
    input_tokens, output, read, writes = counts
    if read + writes > input_tokens:
        return "modern_usage_partition_invalid"
    if event.cache_write_1h_tokens not in {None, 0}:
        return "modern_cache_write_duration_unavailable"
    short, long = event.cache_write_30m_tokens, event.cache_write_12h_tokens
    if writes == 0 and short in {None, 0} and long in {None, 0}:
        short, long = 0, 0
    elif event.event_schema_version != "1.1" or short is None or long is None:
        return "modern_cache_write_duration_unavailable"
    if (
        any(type(value) is not int or value < 0 for value in (short, long))
        or short + long != writes
    ):
        return "modern_usage_partition_invalid"
    large = input_tokens > rate["long_context_threshold"]
    input_multiplier = Decimal(rate["long_context_input_multiplier"]) if large else Decimal(1)
    output_multiplier = Decimal(rate["long_context_output_multiplier"]) if large else Decimal(1)
    buckets = {
        "uncached_input": (input_tokens - read - writes, Decimal(rate["input"]) * input_multiplier),
        "cached_input": (read, Decimal(rate["cache_read"]) * input_multiplier),
        "cache_write_30m": (short, Decimal(rate["cache_write_30m"]) * input_multiplier),
        "cache_write_12h": (long, Decimal(rate["cache_write_12h"]) * input_multiplier),
        "output": (output, Decimal(rate["output"]) * output_multiplier),
    }
    return {
        key: str(Decimal(tokens) * price / Decimal(1000000))
        for key, (tokens, price) in buckets.items()
    }
