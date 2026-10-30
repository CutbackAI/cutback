"""Parse-only qualification of the independent context application's exact wrapper."""

import ast
import hashlib
from typing import Final, Literal

from pydantic import Field, model_validator

from cutback.core.models import Identifier, StrictModel
from cutback.core.request_profiles import safe_relative

# Entire module, including imports, settings loading, builder, call and entry point.
# Only the model and output-cap literals may vary. No source is imported or executed.
PROGRAM_SHA256: Final = "a6175e8cf0f03483bc69efd5e6f49efe0e5f5fc8eed2f6836e08b8175220828e"


def program_dump(node):
    """Preserve the qualified 3.11 representation across AST display changes.

    Empty type parameters were added in 3.12 and empty-list display changed in
    3.13. Nonempty type parameters remain outside this exact wrapper contract.
    """
    if isinstance(node, ast.AST):
        fields = []
        for name, value in ast.iter_fields(node):
            if name == "type_params":
                if value:
                    raise ValueError("CONTEXT_PRODUCER_TYPE_PARAMETERS_NOT_QUALIFIED")
                continue
            if value is None and getattr(type(node), name, ...) is None:
                continue
            fields.append(name + "=" + program_dump(value))
        return type(node).__name__ + "(" + ", ".join(fields) + ")"
    if isinstance(node, list):
        return "[" + ", ".join(program_dump(value) for value in node) + "]"
    return repr(node)


class ContextProducerBinding(StrictModel):
    version: Literal["cutback.context-producer.v1"] = "cutback.context-producer.v1"
    file: str = Field(min_length=1, max_length=240, pattern=r"^[A-Za-z0-9_./-]+$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    commit: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    program_sha256: Literal["a6175e8cf0f03483bc69efd5e6f49efe0e5f5fc8eed2f6836e08b8175220828e"] = (
        PROGRAM_SHA256
    )
    provider: Literal["openai"] = "openai"
    surface: Literal["responses.create"] = "responses.create"
    model: Identifier
    max_output_tokens: int = Field(strict=True, ge=16, le=16384)

    @model_validator(mode="after")
    def safe(self):
        safe_relative(self.file)
        if not self.file.endswith(".py"):
            raise ValueError("CONTEXT_PRODUCER_REQUIRES_PYTHON_FILE")
        return self


def qualify(raw: bytes):
    """Return literal nodes only after the entire bounded program matches."""
    if len(raw) > 65536:
        raise ValueError("CONTEXT_PRODUCER_SOURCE_BOUND")
    try:
        tree = ast.parse(raw.decode("utf-8"))
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "answer"
        )
        returned = function.body[0]
        if not isinstance(returned, ast.Return) or not isinstance(returned.value, ast.Attribute):
            raise ValueError
        call = returned.value.value
        if not isinstance(call, ast.Call):
            raise ValueError
        nodes = {keyword.arg: keyword.value for keyword in call.keywords}
        model, cap = nodes["model"], nodes["max_output_tokens"]
        if not isinstance(model, ast.Constant) or not isinstance(model.value, str):
            raise ValueError
        if not isinstance(cap, ast.Constant) or type(cap.value) is not int:
            raise ValueError
        values = model.value, cap.value
        for keyword in call.keywords:
            if keyword.arg in {"model", "max_output_tokens"}:
                keyword.value = ast.Constant(value="CUTBACK_BOUND_LITERAL")
        actual = hashlib.sha256(program_dump(tree).encode()).hexdigest()
        if actual != PROGRAM_SHA256:
            raise ValueError
    except (
        UnicodeError,
        SyntaxError,
        ValueError,
        AttributeError,
        KeyError,
        StopIteration,
        RecursionError,
    ):
        raise ValueError("CONTEXT_PRODUCER_PROGRAM_NOT_QUALIFIED") from None
    return values, {"model": model, "max_output_tokens": cap}
