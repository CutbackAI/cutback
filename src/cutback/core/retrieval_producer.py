"""Parse-only qualification of the independent local retrieval consumer."""

import ast
import hashlib
from typing import Final, Literal

from pydantic import Field, model_validator

from cutback.core.context_producer import program_dump
from cutback.core.models import Identifier, StrictModel
from cutback.core.request_profiles import safe_relative

PROGRAM_SHA256: Final = "234c2703aa3b4c98b068dab33ba4e19407a9d05ea1275eaf436b0683bb8c7ac0"


class RetrievalProducerBinding(StrictModel):
    version: Literal["cutback.retrieval-producer.v1"] = "cutback.retrieval-producer.v1"
    file: str = Field(min_length=1, max_length=240, pattern=r"^[A-Za-z0-9_./-]+$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    commit: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    program_sha256: Literal["234c2703aa3b4c98b068dab33ba4e19407a9d05ea1275eaf436b0683bb8c7ac0"] = (
        PROGRAM_SHA256
    )
    provider: Literal["openai"] = "openai"
    surface: Literal["responses.create"] = "responses.create"
    model: Identifier
    max_output_tokens: int = Field(strict=True, ge=16, le=16384)
    corpus_file: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def safe(self):
        from pathlib import PurePosixPath

        safe_relative(self.file)
        safe_relative(self.corpus_file)
        if (
            not self.file.endswith(".py")
            or PurePosixPath(self.corpus_file) != PurePosixPath(self.file).parent / "corpus.json"
        ):
            raise ValueError("RETRIEVAL_PRODUCER_REQUIRES_BOUND_LOCAL_CORPUS")
        return self


def qualify(raw: bytes):
    """Only model/output-cap literals vary; no module or corpus is imported."""
    if len(raw) > 65536:
        raise ValueError("RETRIEVAL_PRODUCER_SOURCE_BOUND")
    try:
        tree = ast.parse(raw.decode("utf-8"))
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "answer"
        )
        returned = function.body[-1]
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
        if not 16 <= cap.value <= 16384:
            raise ValueError
        values = model.value, cap.value
        for keyword in call.keywords:
            if keyword.arg in {"model", "max_output_tokens"}:
                keyword.value = ast.Constant(value="CUTBACK_BOUND_LITERAL")
        if hashlib.sha256(program_dump(tree).encode()).hexdigest() != PROGRAM_SHA256:
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
        raise ValueError("RETRIEVAL_PRODUCER_PROGRAM_NOT_QUALIFIED") from None
    return values, {"model": model, "max_output_tokens": cap}
