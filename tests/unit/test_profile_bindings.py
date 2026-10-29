"""Offline source contracts must not turn Python shadowing into a known request."""

import sys

import pytest

from cutback.core.request_profiles import prepare_profile
from cutback.scanner import scan

BASE = """from openai import OpenAI
raise RuntimeError("owned source must not execute")
PROMPT = "owned global instruction"
client = OpenAI()
def classify(text):
    return client.responses.create(model="gpt-4.1", input=text,
                                   instructions=PROMPT, max_output_tokens=64)
"""


def profile(root, source=BASE, path="app.py"):
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text(source, encoding="utf-8")
    sites = scan(root)["call_sites"]
    assert len(sites) == 1
    return prepare_profile(root, sites[0])


@pytest.mark.parametrize(
    "signature",
    [
        "text, PROMPT",
        "text, /, *, PROMPT",
        "text, *PROMPT",
        "text, **PROMPT",
        "text, PROMPT='default'",
    ],
)
def test_instruction_parameter_cannot_fall_back_to_same_named_global(tmp_path, signature):
    with pytest.raises(ValueError, match="REQUIRES_REVIEW"):
        profile(tmp_path, BASE.replace("classify(text)", "classify(" + signature + ")"))


def test_module_alias_parameter_cannot_resolve_the_global_import(tmp_path):
    (tmp_path / "config.py").write_text('PROMPT="owned module instruction"\n')
    source = BASE.replace('PROMPT = "owned global instruction"', "import config as cfg")
    source = source.replace("classify(text)", "classify(text, cfg)").replace(
        "instructions=PROMPT", "instructions=cfg.PROMPT"
    )
    with pytest.raises(ValueError, match="REQUIRES_REVIEW"):
        profile(tmp_path, source)


@pytest.mark.parametrize("field", ["instruction", "payload"])
def test_model_patch_rejects_shared_constant_changing_an_unevaluated_field(tmp_path, field):
    from test_request_profiles import SOURCE, inspect, recommendation

    from cutback.changes import model_patch

    source = SOURCE.replace("client=OpenAI()", 'MODEL="gpt-4.1"\nclient=OpenAI()')
    source = source.replace('model="gpt-4.1"', "model=MODEL")
    if field == "instruction":
        source = source.replace('instructions="Private synthetic task"', "instructions=MODEL")
    else:
        source = source.replace("input=text", "input=MODEL+text")
    inventory, site, prepared = inspect(tmp_path, source)
    original = (tmp_path / "app.py").read_bytes()
    with pytest.raises(ValueError, match="PATCH_CHANGED_UNEVALUATED_REQUEST_CONTRACT"):
        model_patch(
            tmp_path,
            inventory,
            site["call_site_id"],
            recommendation(prepared),
            expected_source_sha256=prepared.binding.source_sha256,
        )
    assert (tmp_path / "app.py").read_bytes() == original


def test_closure_parameter_cannot_fall_back_to_module_instruction(tmp_path):
    source = BASE.replace("def classify(text):", "def outer(PROMPT):\n def classify(text):")
    source = source.replace("    return client", "     return client")
    with pytest.raises(ValueError, match="REQUIRES_REVIEW"):
        profile(tmp_path, source)


@pytest.mark.parametrize(
    "body",
    [
        "for PROMPT in ['changed']: pass",
        "del PROMPT",
        "import config as PROMPT",
        "with owned_context() as PROMPT: pass",
    ],
)
def test_local_binding_cannot_fall_back_to_global_instruction(tmp_path, body):
    source = BASE.replace("    return client", "    " + body + "\n    return client")
    with pytest.raises(ValueError, match="REQUIRES_REVIEW"):
        profile(tmp_path, source)


@pytest.mark.parametrize("prefix", ["", "src/"])
def test_parent_relative_import_resolves_actual_package_not_sibling(tmp_path, prefix):
    for name in ["pkg/__init__.py", "pkg/sub/__init__.py"]:
        path = tmp_path / (prefix + name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    (tmp_path / (prefix + "pkg/config.py")).write_text(
        'PROMPT="owned correct parent instruction"\n'
    )
    (tmp_path / (prefix + "pkg/sub/config.py")).write_text(
        'PROMPT="owned wrong sibling instruction"\n'
    )
    source = BASE.replace('PROMPT = "owned global instruction"', "from ..config import PROMPT")
    result = profile(tmp_path, source, prefix + "pkg/sub/app.py")
    assert result.instruction == "owned correct parent instruction"
    assert prefix + "pkg/config.py" in result.binding.dependencies
    assert prefix + "pkg/sub/config.py" not in result.binding.dependencies


def test_circular_imports_stop_at_a_declared_bound_not_python_recursion_limit(tmp_path):
    (tmp_path / "a.py").write_text("from b import PROMPT\n")
    (tmp_path / "b.py").write_text("from a import PROMPT\n")
    source = BASE.replace('PROMPT = "owned global instruction"', "from a import PROMPT")
    with pytest.raises(ValueError, match="PROFILE_RESOLUTION_LIMIT"):
        profile(tmp_path, source)


@pytest.mark.parametrize("change", ["local", "import"])
def test_unambiguous_static_instruction_still_qualifies_without_execution(tmp_path, change):
    if change == "local":
        source = BASE.replace('PROMPT = "owned global instruction"\n', "")
        source = source.replace(
            "    return client", '    PROMPT="owned local instruction"\n    return client'
        )
        expected = "owned local instruction"
    else:
        (tmp_path / "config.py").write_text('PROMPT="owned imported instruction"\n')
        source = BASE.replace('PROMPT = "owned global instruction"', "from config import PROMPT")
        expected = "owned imported instruction"
    result = profile(tmp_path, source)
    assert result.instruction == expected


@pytest.mark.parametrize(
    "change",
    [
        "alias_local",
        "global_import_rebind",
        "late_global_rebind",
        "late_alias_value",
        "ambiguous_root",
    ],
)
def test_other_shadowed_or_ambiguous_bindings_are_not_treated_as_frozen_constants(tmp_path, change):
    (tmp_path / "config.py").write_text('PROMPT="owned imported instruction"\n')
    if change == "alias_local":
        source = BASE.replace('PROMPT = "owned global instruction"', "import config as cfg")
        source = source.replace("    return client", '    cfg="not a module"\n    return client')
        source = source.replace("instructions=PROMPT", "instructions=cfg.PROMPT")
    elif change == "global_import_rebind":
        source = BASE.replace(
            'PROMPT = "owned global instruction"', 'PROMPT = "old value"\nfrom config import PROMPT'
        )
    elif change == "late_global_rebind":
        source = BASE + '\nPROMPT="owned later runtime value"\n'
    elif change == "late_alias_value":
        source = BASE.replace(
            'PROMPT = "owned global instruction"',
            'PROMPT = LATER\nLATER="not yet bound when alias is assigned"',
        )
    else:
        (tmp_path / "src").mkdir()
        (tmp_path / "src/config.py").write_text('PROMPT="owned competing import root"\n')
        source = BASE.replace('PROMPT = "owned global instruction"', "from config import PROMPT")
    with pytest.raises(ValueError, match="REQUIRES_REVIEW"):
        profile(tmp_path, source)


@pytest.mark.parametrize("mode", ["duplicate_alias", "wildcard", "shadowed_path"])
def test_ambiguous_imports_and_shadowed_prompt_readers_abstain(tmp_path, mode):
    (tmp_path / "config.py").write_text(
        'A="owned first"\nB="owned second"\nPROMPT="owned imported"\n'
    )
    if mode == "duplicate_alias":
        source = BASE.replace(
            'PROMPT = "owned global instruction"', "from config import A as PROMPT, B as PROMPT"
        )
    elif mode == "wildcard":
        source = BASE.replace(
            'PROMPT = "owned global instruction"',
            'PROMPT="owned before wildcard"\nfrom config import *',
        )
    else:
        (tmp_path / "prompt.txt").write_text("owned local instruction")
        source = BASE.replace('PROMPT = "owned global instruction"', "from pathlib import Path")
        source = source.replace("classify(text)", "classify(text, Path)")
        source = source.replace(
            "instructions=PROMPT", 'instructions=Path("prompt.txt").read_text()'
        )
    with pytest.raises(ValueError, match="REQUIRES_REVIEW"):
        profile(tmp_path, source)


@pytest.mark.parametrize("scope", ["lambda", "class"])
def test_unsupported_expression_scopes_cannot_fall_back_to_module_variables(tmp_path, scope):
    header = BASE.split("def classify")[0]
    call = (
        'client.responses.create(model="gpt-4.1",input="fixed",'
        "instructions=PROMPT,max_output_tokens=64)"
    )
    if scope == "lambda":
        source = header + "classify = lambda PROMPT: " + call + "\n"
    else:
        source = (
            header + 'class Owner:\n PROMPT="owned class instruction"\n response=' + call + "\n"
        )
    with pytest.raises(ValueError, match="REQUIRES_REVIEW"):
        profile(tmp_path, source)


@pytest.mark.skipif(
    sys.version_info < (3, 12), reason="PEP 695 syntax requires the separate Python 3.13 matrix"
)
def test_type_parameter_is_not_a_same_named_global_instruction(tmp_path):
    with pytest.raises(ValueError, match="REQUIRES_REVIEW"):
        profile(tmp_path, BASE.replace("def classify(text)", "def classify[PROMPT](text)"))


def test_duplicate_request_keywords_do_not_become_a_valid_replay_contract(tmp_path):
    with pytest.raises(ValueError, match="DUPLICATE_REQUEST_ARGUMENTS"):
        profile(
            tmp_path,
            BASE.replace("instructions=PROMPT", 'instructions=PROMPT,instructions="other"'),
        )


def test_patch_recompilation_rejects_changed_dependency_even_if_values_look_unchanged(
    tmp_path, monkeypatch
):
    from test_request_profiles import SOURCE, inspect, recommendation

    from cutback.changes import model_patch
    from cutback.core import request_profiles

    dependency = tmp_path / "config.py"
    dependency.write_bytes(b'SYSTEM="owned independent instruction"\n')
    source = SOURCE.replace("client=OpenAI()", "from config import SYSTEM\nclient=OpenAI()")
    source = source.replace('instructions="Private synthetic task"', "instructions=SYSTEM")
    inventory, site, prepared = inspect(tmp_path, source)
    before = (tmp_path / "app.py").read_bytes()
    original = request_profiles.prepare_profile

    def changed_between_compilations(*args, **kwargs):
        if kwargs.get("source_overrides"):
            dependency.write_bytes(dependency.read_bytes() + b"# owned concurrent edit\n")
        return original(*args, **kwargs)

    monkeypatch.setattr(request_profiles, "prepare_profile", changed_between_compilations)
    with pytest.raises(ValueError, match="PATCH_SOURCE_DEPENDENCIES_CHANGED_DURING_REVIEW"):
        model_patch(
            tmp_path,
            inventory,
            site["call_site_id"],
            recommendation(prepared),
            expected_source_sha256=prepared.binding.source_sha256,
        )
    assert (tmp_path / "app.py").read_bytes() == before
