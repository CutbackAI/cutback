"""Synthetic provider-shaped payload; no real inference."""

SYNTHETIC_RESPONSE = {
    "id": "resp_synthetic",
    "object": "response",
    "created_at": 1,
    "status": "completed",
    "model": "gpt-4.1-mini-2025-04-14",
    "output": [],
    "usage": {
        "input_tokens": 1000,
        "output_tokens": 100,
        "total_tokens": 1100,
        "input_tokens_details": {"cached_tokens": 200},
        "output_tokens_details": {"reasoning_tokens": 20},
    },
}
