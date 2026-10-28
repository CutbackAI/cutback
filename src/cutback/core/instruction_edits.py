"""Parse-only qualification of one exact native instruction literal/insertion point."""

import ast
import hashlib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StrictInt, model_validator

from cutback.core.models import StrictModel
from cutback.core.request_profiles import SourceBinding, safe_relative

Offset = Annotated[StrictInt, Field(ge=0, le=1048576)]


class LiteralInstructionEdit(StrictModel):
    version: Literal["literal-instruction-edit-v1"] = "literal-instruction-edit-v1"
    source_profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_file: str = Field(max_length=500)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    keyword: Literal["instructions", "system"]
    call_index: Annotated[StrictInt, Field(ge=0, le=200000)]
    mode: Literal["replace", "insert"]
    start: Offset
    end: Offset

    @model_validator(mode="after")
    def bounded(self):
        safe_relative(self.source_file)
        if self.end < self.start or (self.mode == "insert") != (self.start == self.end):
            raise ValueError("INSTRUCTION_EDIT_RANGE_INVALID")
        return self

    def require_source(self, binding: SourceBinding):
        if (
            self.source_profile_sha256 != binding.profile_sha256
            or self.source_file != binding.source_file
            or self.source_sha256 != binding.source_sha256
            or binding.model_binding["file"] != self.source_file
        ):
            raise ValueError("INSTRUCTION_EDIT_SOURCE_PROFILE_MISMATCH")


def byte_range(raw, node):
    if node.end_lineno is None or node.end_col_offset is None:
        raise ValueError("INSTRUCTION_EDIT_SOURCE_RANGE_UNAVAILABLE")
    lines = raw.splitlines(keepends=True)
    return (
        sum(map(len, lines[: node.lineno - 1])) + node.col_offset,
        sum(map(len, lines[: node.end_lineno - 1])) + node.end_col_offset,
    )


def qualify_instruction_edit(root: Path, site, profile):
    """No source execution or mutation. Imported/dynamic edits remain explicit gaps."""
    root = root.resolve(strict=True)
    binding = profile.binding
    if binding.model_binding["file"] != binding.source_file:
        raise ValueError("INSTRUCTION_EDIT_REQUIRES_QUALIFIED_SAME_FILE_REQUEST")
    path = root / binding.source_file
    if (
        path.is_symlink()
        or not path.resolve().is_relative_to(root.resolve())
        or any(p.is_symlink() for p in path.parents if p.is_relative_to(root))
        or path.stat().st_size > 1048576
    ):
        raise ValueError("INSTRUCTION_EDIT_SOURCE_PATH_UNSAFE")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != binding.source_sha256:
        raise ValueError("INSTRUCTION_EDIT_SOURCE_CHANGED")
    surface = profile.request.sdk_surface
    if surface not in {"responses.create", "messages.create"}:
        raise ValueError("INSTRUCTION_EDIT_NATIVE_SURFACE_NOT_QUALIFIED")
    all_calls = [
        n
        for n in ast.walk(ast.parse(raw.decode("utf-8")))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "create"
    ]
    calls = [
        n
        for n in all_calls
        if isinstance(n, ast.Call)
        and n.lineno == site["line"]
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "create"
    ]
    if len(calls) != 1 or calls[0].args or any(k.arg is None for k in calls[0].keywords):
        raise ValueError("INSTRUCTION_EDIT_REQUEST_NOT_UNIQUE")
    call = calls[0]
    keywords = [k.arg for k in call.keywords]
    if len(keywords) != len(set(keywords)):
        raise ValueError("INSTRUCTION_EDIT_DUPLICATE_REQUEST_KEYWORDS")
    models = [k.value for k in call.keywords if k.arg == "model"]
    edit = binding.model_binding
    literals = [
        n
        for n in ast.walk(ast.parse(raw.decode("utf-8")))
        if isinstance(n, ast.Constant)
        and n.value == profile.model
        and (n.lineno, n.col_offset) == (edit["line"], edit["column"])
    ]
    if (
        len(models) != 1
        or not isinstance(models[0], (ast.Constant, ast.Name))
        or len(literals) != 1
    ):
        raise ValueError("INSTRUCTION_EDIT_REQUIRES_LITERAL_BOUND_MODEL")
    keyword: Literal["instructions", "system"] = (
        "instructions" if surface == "responses.create" else "system"
    )
    mode: Literal["replace", "insert"]
    instructions = [k.value for k in call.keywords if k.arg == keyword]
    if instructions:
        node = instructions[0]
        if (
            not isinstance(node, ast.Constant)
            or not isinstance(node.value, str)
            or node.value != profile.instruction
        ):
            raise ValueError("DYNAMIC_INSTRUCTION_EDIT_REQUIRES_SEPARATE_ADAPTER")
        start, end = byte_range(raw, node)
        mode = "replace"
    else:
        if profile.instruction or not call.keywords:
            raise ValueError("INSTRUCTION_EDIT_BASELINE_MISMATCH")
        _, end = byte_range(raw, call.keywords[-1].value)
        start, mode = end, "insert"
    result = LiteralInstructionEdit(
        source_profile_sha256=binding.profile_sha256,
        source_file=binding.source_file,
        source_sha256=binding.source_sha256,
        keyword=keyword,
        call_index=all_calls.index(call),
        mode=mode,
        start=start,
        end=end,
    )
    result.require_source(binding)
    return result
