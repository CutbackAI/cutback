"""Parse-only qualification of the fixed common source-policy consumer.

The producer is application-owned source. Qualification parses its complete AST;
it never imports the application, reads private runtime credentials or makes I/O
to a provider. Collector/experiment approval remains a separate requirement.
"""

import ast
import hashlib
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from cutback.core.context_producer import program_dump
from cutback.core.models import Identifier, StrictModel
from cutback.core.request_profiles import safe_relative

PROGRAM = """import os
from pathlib import Path

from cutback.source_policy import load_policy, responses_create

ROOT = Path(__file__).resolve().parent


def classify(client, text):
    policy = load_policy(ROOT / "policy.json")
    scope_file = (
        os.environ.get("CUTBACK_NATIVE_CACHE_SCOPE_FILE") if policy.cache_execution else None
    )
    return responses_create(client, text, policy, scope_file=scope_file)
"""
PROGRAM_AST = program_dump(ast.parse(PROGRAM))
PROGRAM_SHA256 = hashlib.sha256(PROGRAM_AST.encode()).hexdigest()


def qualify(raw: bytes):
    if type(raw) is not bytes or len(raw) > 65536:
        raise ValueError("POLICY_SOURCE_PRODUCER_BYTE_BOUND")
    try:
        tree = ast.parse(raw.decode("utf-8"))
        if len(list(ast.walk(tree))) > 2000:
            raise ValueError
        if tree.body and isinstance(tree.body[0], ast.Expr):
            first = tree.body[0].value
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                tree.body.pop(0)
        if program_dump(tree) != PROGRAM_AST:
            raise ValueError
    except (ValueError, UnicodeError, SyntaxError, RecursionError):
        raise ValueError("POLICY_SOURCE_UNQUALIFIED_NATIVE_PROGRAM") from None
    return PROGRAM_SHA256


class PolicySourceBinding(StrictModel):
    version: Literal["cutback.policy-source.v1"] = "cutback.policy-source.v1"
    producer_file: str = Field(min_length=1, max_length=240, pattern=r"^[A-Za-z0-9_./-]+$")
    producer_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    configuration_file: str = Field(min_length=1, max_length=240, pattern=r"^[A-Za-z0-9_./-]+$")
    configuration_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    commit: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    program_sha256: str = Field(default=PROGRAM_SHA256, pattern=r"^[a-f0-9]{64}$")
    workflow: Identifier
    max_input_bytes: int = Field(strict=True, ge=1, le=95904)
    provider: Literal["openai"] = "openai"
    surface: Literal["responses.create"] = "responses.create"

    @model_validator(mode="after")
    def paired_source(self):
        safe_relative(self.producer_file)
        safe_relative(self.configuration_file)
        if (
            not self.producer_file.endswith(".py")
            or str(PurePosixPath(self.producer_file).parent / "policy.json")
            != self.configuration_file
            or self.program_sha256 != PROGRAM_SHA256
        ):
            raise ValueError("POLICY_SOURCE_EXACT_PAIRED_PROGRAM_AND_CONFIGURATION_REQUIRED")
        return self
