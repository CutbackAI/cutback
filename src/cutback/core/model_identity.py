"""Bounded provider-reported model labels, never reconstructed from configuration."""

import re

_IDENTIFIER = re.compile(r"[a-zA-Z0-9_.:/-]{1,200}\Z")
_CREDENTIAL = re.compile(
    r"(?:sk[-_]|bearer|gh[pousr]_|github_pat_|AIza|AKIA|ASIA|xox[baprs]-)", re.IGNORECASE
)
_JWT = re.compile(r"[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\Z")


def reported_model_identifier(value: object) -> str | None:
    if type(value) is not str or not _IDENTIFIER.fullmatch(value):
        return None
    if (
        _CREDENTIAL.match(value)
        or _JWT.fullmatch(value)
        or re.fullmatch(r"[a-fA-F0-9]{32,}", value)
        or re.match(r"[A-Za-z]:/", value)
        or value.startswith(("/", "../"))
        or "/../" in value
    ):
        return None
    return value
