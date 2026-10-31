"""Parse-only qualification of a bounded native Responses policy consumer."""

import ast
import hashlib
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from cutback.core.context_producer import program_dump
from cutback.core.models import Identifier, StrictModel
from cutback.core.request_profiles import safe_relative

# The application owns its native client. This wrapper preserves its response,
# errors and configured retries; it never retries or switches a target itself.
PROGRAM = """import json
from pathlib import Path

from cutback.evals.experiments import ModelSpec, qualified_native_model, route_target

ROOT = Path(__file__).resolve().parent


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ROUTING_SOURCE_DUPLICATE_CONFIGURATION_KEY")
        result[key] = value
    return result


def classify(client, text):
    from importlib.metadata import version

    from openai import OpenAI

    if type(text) is not str or len(text) > 95904:
        raise ValueError("ROUTING_SOURCE_REQUIRES_BOUNDED_TEXT_INPUT")
    if type(client) is not OpenAI or version("openai") not in {"2.29.0", "3.35.1"}:
        raise ValueError("ROUTING_SOURCE_REQUIRES_QUALIFIED_SYNC_OPENAI_CLIENT")
    if str(client.base_url) != "https://api.openai.com/v1/":
        raise ValueError("ROUTING_SOURCE_REQUIRES_OFFICIAL_NATIVE_ENDPOINT")
    try:
        with (ROOT / "routing.json").open("rb") as source:
            raw = source.read(262145)
        if len(raw) > 262144:
            raise ValueError
        policy = ModelSpec.model_validate(
            json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
        )
    except (OSError, ValueError, RecursionError):
        raise ValueError("ROUTING_SOURCE_CONFIGURATION_UNAVAILABLE_OR_INVALID") from None
    target = route_target(policy, text)
    if (
        target.provider != "openai"
        or target.request_contract
        or target.native_contract
        or target.prompt_cache
        or target.agent_turn
    ):
        raise ValueError("ROUTING_SOURCE_TARGET_CONTRACT_UNSUPPORTED")
    qualified_native_model(target)
    return client.responses.create(
        model=target.model,
        input=text,
        instructions=target.instruction,
        max_output_tokens=target.max_output_tokens,
        store=False,
    )
"""
PROGRAM_SHA256 = hashlib.sha256(program_dump(ast.parse(PROGRAM)).encode()).hexdigest()


class RoutingSourceBinding(StrictModel):
    version: Literal["cutback.routing-source.v1"] = "cutback.routing-source.v1"
    producer_file: str = Field(min_length=1, max_length=240, pattern=r"^[A-Za-z0-9_./-]+$")
    producer_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    settings_file: str = Field(min_length=1, max_length=240, pattern=r"^[A-Za-z0-9_./-]+$")
    settings_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    commit: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    program_sha256: str = Field(default=PROGRAM_SHA256, pattern=r"^[a-f0-9]{64}$")
    configuration_key: Identifier
    configuration_model: Identifier
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    baseline_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    max_input_bytes: int = Field(strict=True, ge=1, le=95904)
    provider: Literal["openai"] = "openai"
    surface: Literal["responses.create"] = "responses.create"

    @model_validator(mode="after")
    def safe(self):
        safe_relative(self.producer_file)
        safe_relative(self.settings_file)
        if (
            not self.producer_file.endswith(".py")
            or str(PurePosixPath(self.producer_file).parent / "routing.json") != self.settings_file
            or self.program_sha256 != PROGRAM_SHA256
        ):
            raise ValueError("ROUTING_SOURCE_REQUIRES_QUALIFIED_PAIRED_PRODUCER_AND_POLICY")
        return self


def qualify(raw: bytes):
    """Accept the complete fixed program; comments/docstrings never execute."""
    if len(raw) > 65536:
        raise ValueError("ROUTING_SOURCE_PRODUCER_BYTE_BOUND")
    try:
        tree = ast.parse(raw.decode("utf-8"))
        if len(list(ast.walk(tree))) > 2000:
            raise ValueError
        if tree.body and isinstance(tree.body[0], ast.Expr):
            first = tree.body[0].value
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                tree.body.pop(0)
        hashed = hashlib.sha256(program_dump(tree).encode()).hexdigest()
    except (SyntaxError, UnicodeError, ValueError, RecursionError):
        raise ValueError("ROUTING_SOURCE_PRODUCER_NOT_QUALIFIED") from None
    if hashed != PROGRAM_SHA256:
        raise ValueError("ROUTING_SOURCE_PRODUCER_NOT_QUALIFIED")
    return hashed
