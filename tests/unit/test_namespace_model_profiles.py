"""Qualified local module constants, without executing application or config code."""

import pytest

from cutback.core.request_profiles import prepare_profile
from cutback.scanner import scan


def application(root, declaration="import config as cfg", signature="text"):
    (root / "app.py").write_text(
        "from openai import OpenAI\n" + declaration + "\n"
        'raise RuntimeError("OWNED_SOURCE_MUST_NOT_EXECUTE")\n'
        "client=OpenAI()\n"
        "def classify(" + signature + "):\n"
        "    return client.responses.create(model=cfg.MODEL, input=text, max_output_tokens=64)\n",
        encoding="utf-8",
        newline="\n",
    )


@pytest.mark.parametrize("package", [False, True])
@pytest.mark.parametrize("alias", ["cfg", "config"])
def test_namespace_constant_binds_exact_module_dependency_without_import(tmp_path, package, alias):
    relative = "settings/config.py" if package else "config.py"
    target = tmp_path / relative
    target.parent.mkdir(exist_ok=True)
    target.write_text(
        'raise RuntimeError("OWNED_CONFIG_MUST_NOT_EXECUTE")\nMODEL="gpt-4.1"\n',
        encoding="utf-8",
        newline="\n",
    )
    if package:
        (tmp_path / "settings/__init__.py").write_text("", encoding="utf-8")
    declaration = (
        ("import settings.config as " + alias)
        if package
        else ("import config" if alias == "config" else "import config as " + alias)
    )
    application(tmp_path, declaration)
    if alias != "cfg":
        source = tmp_path / "app.py"
        source.write_text(
            source.read_text().replace("cfg.MODEL", alias + ".MODEL"),
            encoding="utf-8",
            newline="\n",
        )
    site = scan(tmp_path)["call_sites"][0]
    assert site["requested_model"] == "gpt-4.1"
    assert site["model_reference"]["kind"] == "config_symbol"
    prepared = prepare_profile(tmp_path, site)
    assert prepared.model == "gpt-4.1"
    assert prepared.binding.model_binding["file"] == relative
    assert set(prepared.binding.dependencies) == {"app.py", relative}


@pytest.mark.parametrize("signature", ["text, cfg", "text, /, *, cfg", "text, *cfg", "text, **cfg"])
def test_namespace_parameter_never_uses_same_named_global_import(tmp_path, signature):
    (tmp_path / "config.py").write_text('MODEL="gpt-4.1"\n', encoding="utf-8")
    application(tmp_path, signature=signature)
    site = scan(tmp_path)["call_sites"][0]
    assert site["requested_model"] is None
    with pytest.raises(ValueError, match="REQUIRES_REVIEW"):
        prepare_profile(tmp_path, site)


@pytest.mark.parametrize(
    "body",
    [
        'MODEL="gpt-4.1"\nMODEL="gpt-4.1-mini"\n',
        'import os\nMODEL=os.environ["OWNED_MODEL"]\n',
        'def __getattr__(name):\n    return "gpt-4.1"\n',
        "import config as cfg\nMODEL=cfg.MODEL\n",
    ],
)
def test_ambiguous_dynamic_and_cyclic_namespace_models_stay_unknown(tmp_path, body):
    (tmp_path / "config.py").write_text(body, encoding="utf-8", newline="\n")
    application(tmp_path)
    site = scan(tmp_path)["call_sites"][0]
    assert site["requested_model"] is None
    with pytest.raises(ValueError):
        prepare_profile(tmp_path, site)


def test_namespace_resolution_never_follows_external_config(tmp_path):
    application(tmp_path, "import owned_external_config as cfg")
    site = scan(tmp_path)["call_sites"][0]
    assert site["requested_model"] is None
    with pytest.raises(ValueError):
        prepare_profile(tmp_path, site)


def test_known_namespace_name_does_not_invent_a_price(tmp_path):
    (tmp_path / "config.py").write_text('MODEL="owned-unknown-model"\n', encoding="utf-8")
    application(tmp_path)
    site = scan(tmp_path)["call_sites"][0]
    assert site["requested_model"] == "owned-unknown-model"
    assert site["price"]["rate"] is None
    assert prepare_profile(tmp_path, site).model == "owned-unknown-model"
