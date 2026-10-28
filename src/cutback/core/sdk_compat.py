"""Exact, separately qualified SDK versions; no provider imports or version ranges."""

OPENAI_TEXT_SDKS = frozenset({"2.29.0", "3.35.1"})
OPENAI_OBSERVER_SDKS = OPENAI_TEXT_SDKS | {"1.55.3"}
