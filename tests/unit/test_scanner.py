import json
from pathlib import Path

import pytest

from cutback.scanner import scan


def write(root, text, name="main.py"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize(
    "constructor,surface,provider",
    [
        ("from openai import OpenAI as C", "responses.create", "openai"),
        ("from openai import AsyncOpenAI as C", "chat.completions.create", "openai"),
        ("from anthropic import Anthropic as C", "messages.create", "anthropic"),
        ("from anthropic import AsyncAnthropic as C", "messages.create", "anthropic"),
    ],
)
def test_direct_alias_and_no_execution(tmp_path, constructor, surface, provider):
    write(
        tmp_path,
        f'{constructor}\nraise RuntimeError("MUST NOT EXECUTE")\nc = C()\n'
        f'c.{surface}(model="gpt-4.1-mini", input="PRIVATE-PROMPT")\n',
    )
    result = scan(tmp_path)
    (site,) = result["call_sites"]
    assert site["provider"] == provider
    assert site["requested_model"] == "gpt-4.1-mini"
    assert site["actual_cost"] is site["actual_tokens"] is site["actual_latency"] is None
    assert "PRIVATE-PROMPT" not in json.dumps(result)


def test_cross_file_wrapper_and_dynamic_model(tmp_path):
    write(tmp_path, "from openai import OpenAI\nc = OpenAI()\n", "clients.py")
    write(
        tmp_path,
        "from clients import c\nimport os\ndef shared(prompt):\n"
        '    return c.responses.create(model=os.getenv("APP_MODEL"), input=prompt)\n'
        'def route_a():\n    return shared("secret-a")\n'
        'def route_b():\n    return shared("secret-b")\n',
    )
    (site,) = scan(tmp_path)["call_sites"]
    assert site["requested_model"] is None
    assert site["model_reference"]["environment_names"] == ["APP_MODEL"]
    assert site["upstream_callers"] == ["main.route_a", "main.route_b"]
    assert site["workflow_attribution"] == "heuristic"


def test_constants_and_fingerprint_stable_on_line_shift(tmp_path):
    text = (
        'import openai as ai\nMODEL="gpt-4.1-mini"\nc=ai.OpenAI()\n'
        'c.responses.create(model=MODEL, input="x")\n'
    )
    write(tmp_path, text)
    (first,) = scan(tmp_path)["call_sites"]
    write(tmp_path, "\n\n" + text)
    (second,) = scan(tmp_path)["call_sites"]
    assert first["call_site_id"] == second["call_site_id"]
    assert second["line"] == first["line"] + 2
    assert second["requested_model"] == "gpt-4.1-mini"


def test_shadowed_names_unrelated_methods_comments_and_ambiguous_assignments(tmp_path):
    write(
        tmp_path,
        'from openai import OpenAI\n# OpenAI().responses.create(model="x")\n'
        'class OpenAI:\n    pass\nc=OpenAI()\nc.responses.create(model="x")\n'
        'unrelated.create(model="x")\n',
    )
    assert not scan(tmp_path)["call_sites"]
    write(
        tmp_path,
        'from openai import OpenAI\nc=OpenAI()\nMODEL="a"\n'
        'if flag:\n    MODEL="b"\nc.responses.create(model=MODEL, input="x")',
    )
    assert scan(tmp_path)["call_sites"][0]["requested_model"] is None


def test_resource_limits_syntax_secret_ignores_and_symlink(tmp_path):
    write(tmp_path, "not valid Python !!!", "broken.py")
    write(tmp_path, "x=" + repr("x" * 1_048_576), "large.py")
    write(tmp_path, 'print("SHOULD-NOT-BE-READ")', ".venv/hidden.py")
    write(tmp_path, "SECRET=PRIVATE", ".env")
    write(tmp_path, "export const x = 1;", "app.ts")
    outside = tmp_path.parent / (tmp_path.name + "-outside.py")
    outside.write_text("raise RuntimeError('private')")
    try:
        (tmp_path / "escape.py").symlink_to(outside)
    except OSError:
        pass
    result = scan(tmp_path)
    assert result["coverage"]["analyzed_files"] == 0
    codes = {w["code"] for w in result["warnings"]}
    assert {"UNPARSED_FILE", "RESOURCE_LIMIT", "UNSUPPORTED_LANGUAGES"} <= codes
    assert "PRIVATE" not in json.dumps(result)


def test_examples():
    result = scan(Path(__file__).parents[2] / "examples/fastapi-mixed-providers")
    assert len(result["call_sites"]) == 2


def test_root_gitignore_and_config_exclusions(tmp_path):
    source = (
        "from openai import OpenAI\nc=OpenAI()\n"
        'c.responses.create(model="gpt-4.1-mini", input="private")\n'
    )
    write(tmp_path, "ignored.py\nignored_directory/\n", ".gitignore")
    write(tmp_path, source, "ignored.py")
    write(tmp_path, source, "ignored_directory/main.py")
    write(tmp_path, source, "configured.py")
    write(tmp_path, source, "kept.py")
    write(tmp_path, "version: 1\nscanner:\n  exclude: [configured.py]\n", ".cutback/config.yaml")
    (site,) = scan(tmp_path)["call_sites"]
    assert site["source_file"] == "kept.py"


def test_template_reference_is_not_read_or_executed(tmp_path):
    write(
        tmp_path,
        "from pathlib import Path\nfrom openai import OpenAI\n"
        'PROMPT=Path("prompts/private.j2").read_text()\nc=OpenAI()\n'
        'c.responses.create(model="gpt-4.1-mini", input=PROMPT)\n',
    )
    write(tmp_path, "PRIVATE-RAW-TEMPLATE", "prompts/private.j2")
    result = scan(tmp_path)
    (site,) = result["call_sites"]
    assert site["prompt_template_refs"][0]["template_path"] == "prompts/private.j2"
    assert "PRIVATE-RAW-TEMPLATE" not in json.dumps(result)


def test_imported_model_constant_and_redacted_runtime_expression(tmp_path):
    write(tmp_path, 'MODEL="gpt-4.1-mini"', "config.py")
    write(
        tmp_path,
        "from config import MODEL\nfrom openai import OpenAI\nc=OpenAI()\n"
        'c.responses.create(model=MODEL, input="private")\n'
        'c.responses.create(model=config.get("PRIVATE-CONFIG-PATH"), input="private")\n',
    )
    result = scan(tmp_path)
    assert result["call_sites"][0]["requested_model"] == "gpt-4.1-mini"
    assert result["call_sites"][1]["requested_model"] is None
    assert "PRIVATE-CONFIG-PATH" not in json.dumps(result)
