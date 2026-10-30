"""Exception text is never an output, including strings shaped like safe codes."""

import pytest

from cutback.server.semantic_cache_api import public_lookup_reason


@pytest.mark.parametrize(
    "message",
    [
        "SYNTHETIC_PRIVATE_EXCEPTION_DETAILS",
        "file /synthetic/private",
        "SEMANTIC_CACHE_LOOKUP_UNAVAILABLE_EXTRA",
        "",
        "Z" * 100,
    ],
)
def test_unknown_lookup_exceptions_select_one_fixed_public_response(message):
    assert public_lookup_reason(ValueError(message)) == "SEMANTIC_CACHE_LOOKUP_UNAVAILABLE"


def test_current_key_and_policy_denials_preserve_explicit_public_reason_values():
    for code in (
        "SEMANTIC_CACHE_SERVING_KEY_OR_SCOPE_DENIED",
        "SEMANTIC_CACHE_CAPTURE_POLICY_CHANGED_REAPPROVAL_REQUIRED",
    ):
        assert public_lookup_reason(ValueError(code)) == code
