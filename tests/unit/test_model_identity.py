"""Only bounded categorical model metadata enters execution receipts."""

import pytest

from cutback.core.model_identity import reported_model_identifier


@pytest.mark.parametrize("value", ["gpt-4.1-2025-04-14", "vendor/model-v2:latest", "Model_One"])
def test_reported_model_is_preserved_without_alias_or_case_inference(value):
    assert reported_model_identifier(value) == value


@pytest.mark.parametrize(
    "value",
    [
        None,
        3,
        True,
        {},
        "",
        "a" * 201,
        "private content",
        "../private",
        "/private",
        "C:/private",
        "model/../private",
        "sk-synthetic",
        "github_pat_synthetic",
        "BearerSynthetic",
        "a" * 32,
        "a" * 12 + "." + "b" * 12 + "." + "c" * 12,
    ],
    ids=[
        "missing",
        "integer",
        "boolean",
        "mapping",
        "empty",
        "oversize",
        "text",
        "relative",
        "absolute",
        "drive",
        "traversal",
        "key",
        "github",
        "bearer",
        "hex",
        "jwt",
    ],
)
def test_unqualified_response_metadata_is_withheld(value):
    assert reported_model_identifier(value) is None
