"""AST display changes cannot expand or break the exact parse-only contract."""

import ast
from pathlib import Path

import pytest

from cutback.core.context_producer import program_dump, qualify


def test_qualified_program_uses_fixed_representation_and_rejects_extra_semantics():
    source = Path(__file__).resolve().parents[2] / "examples/context-compression/app.py"
    raw = source.read_bytes()
    assert qualify(raw)[0] == ("gpt-4.1-mini", 128)
    with pytest.raises(ValueError, match="NOT_QUALIFIED"):
        qualify(raw + b"\nextra = 1\n")


def test_empty_type_parameters_preserve_identity_but_nonempty_are_rejected():
    node = ast.parse("def f():\n    return []\n")
    before = program_dump(node)
    function = node.body[0]
    if "type_params" not in function._fields:
        function._fields = (*function._fields, "type_params")
    function.type_params = []
    assert program_dump(node) == before
    function.type_params = [ast.Name(id="T", ctx=ast.Load())]
    with pytest.raises(ValueError, match="TYPE_PARAMETERS_NOT_QUALIFIED"):
        program_dump(node)
