"""A deliberately simple shared synthetic consumer contract."""

from pathlib import Path


def normalize(value):
    prompt = Path(__file__).with_name("prompt.txt").read_text(encoding="utf-8")
    if "case-insensitive" not in prompt:
        raise ValueError("UNSUPPORTED_SYNTHETIC_PROMPT_CONTRACT")
    return value.strip().lower()
