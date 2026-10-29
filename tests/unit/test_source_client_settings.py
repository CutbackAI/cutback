"""Static source replay cannot silently replace an explicitly configured destination."""

import pytest

from cutback.core.request_profiles import prepare_profile
from cutback.scanner import scan


def inspect(root, setup, *, body=None):
    source = 'raise RuntimeError("OWNED_SOURCE_MUST_NOT_EXECUTE")\n' + setup + "\n"
    source += body or (
        "def classify(text):\n"
        '    return client.responses.create(model="gpt-4.1", input=text, max_output_tokens=64)\n'
    )
    (root / "app.py").write_text(source, encoding="utf-8", newline="\n")
    site = scan(root)["call_sites"][0]
    return prepare_profile(root, site)


@pytest.mark.parametrize(
    "setup",
    [
        'from openai import OpenAI\nclient=OpenAI(base_url="https://owned.invalid/v1")',
        'from openai import OpenAI as LLM\nclient=LLM(base_url="https://owned.invalid/v1")',
        'import openai as vendor\nclient=vendor.OpenAI(base_url="https://owned.invalid/v1")',
        'from openai import OpenAI\nFactory=OpenAI\nclient=Factory(base_url="https://owned.invalid/v1")',
        'import openai as vendor\nFactory=vendor.OpenAI\nclient=Factory(base_url="https://owned.invalid/v1")',
        'import openai as vendor\nsdk=vendor\nclient=sdk.OpenAI(base_url="https://owned.invalid/v1")',
        'from openai import OpenAI\noptions={"base_url":"https://owned.invalid/v1"}\nclient=OpenAI(**options)',
    ],
)
def test_aliased_and_expanded_client_destinations_require_review(tmp_path, setup):
    with pytest.raises(ValueError, match="CUSTOM_PROVIDER_DESTINATION_REQUIRES_REVIEW") as error:
        inspect(tmp_path, setup)
    assert "owned.invalid" not in str(error.value)


@pytest.mark.parametrize(
    "mutation",
    [
        'client.base_url="https://owned.invalid/v1"',
        'client._base_url="https://owned.invalid/v1"',
        'setattr(client,"base_url","https://owned.invalid/v1")',
        'name="base_url"\nsetattr(client,name,"https://owned.invalid/v1")',
        "del client.base_url",
        'alias=client\nalias.base_url="https://owned.invalid/v1"',
    ],
)
def test_direct_client_destination_mutation_is_not_a_default_provider_contract(tmp_path, mutation):
    with pytest.raises(ValueError, match="CUSTOM_PROVIDER_DESTINATION_REQUIRES_REVIEW"):
        inspect(tmp_path, "from openai import OpenAI\nclient=OpenAI()\n" + mutation)


@pytest.mark.parametrize(
    "setup",
    [
        "from openai import OpenAI\nclient=OpenAI()",
        "from openai import OpenAI as LLM\nclient=LLM()",
        "import openai as vendor\nclient=vendor.OpenAI()",
        'from openai import OpenAI\nimport httpx\ntransport=httpx.Client(base_url="https://unrelated.invalid")\nclient=OpenAI()',
    ],
)
def test_native_default_constructor_aliases_remain_parse_only(tmp_path, setup):
    assert inspect(tmp_path, setup).model == "gpt-4.1"


@pytest.mark.parametrize("constructor", ["OpenAI", "AsyncOpenAI", "Anthropic", "AsyncAnthropic"])
@pytest.mark.parametrize("custom", [False, True])
def test_supported_constructor_families_use_the_same_destination_boundary(
    tmp_path, constructor, custom
):
    anthropic = "Anthropic" in constructor
    package = "anthropic" if anthropic else "openai"
    model = "claude-sonnet-4-6" if anthropic else "gpt-4.1"
    arguments = 'base_url="https://owned.invalid/v1"' if custom else ""
    setup = f"from {package} import {constructor} as SDK\nclient=SDK({arguments})"
    body = (
        "def classify(text):\n"
        f'    return client.messages.create(model="{model}", max_tokens=64, '
        'messages=[{"role":"user","content":text}])\n'
        if anthropic
        else None
    )
    if custom:
        with pytest.raises(ValueError, match="CUSTOM_PROVIDER_DESTINATION_REQUIRES_REVIEW"):
            inspect(tmp_path, setup, body=body)
    else:
        assert inspect(tmp_path, setup, body=body).model == model


def test_imported_constructor_alias_with_explicit_destination_is_rejected(tmp_path):
    (tmp_path / "exports.py").write_text(
        "from openai import OpenAI as ExportedSDK\n", encoding="utf-8", newline="\n"
    )
    with pytest.raises(ValueError, match="CUSTOM_PROVIDER_DESTINATION_REQUIRES_REVIEW"):
        inspect(
            tmp_path,
            'from exports import ExportedSDK as SDK\nclient=SDK(base_url="https://owned.invalid/v1")',
        )


def test_agent_reports_safe_destination_guidance_without_source_or_url(tmp_path):
    import json

    from cutback.agent import analyze

    with pytest.raises(ValueError):
        inspect(
            tmp_path,
            'from openai import OpenAI as LLM\nclient=LLM(base_url="https://owned.invalid/v1")',
        )
    state = analyze(tmp_path)
    assert state["provider_calls"] == 0 and state["profiles"] == []
    assert state["next_action"]["code"] == "QUALIFY_CLIENT_DESTINATION"
    assert state["next_action"]["effects"] == ["read_source_metadata"]
    issue = state["unresolved"][0]
    assert issue["reason"] == "REQUEST_REQUIRES_INSPECTED_ADAPTER_OR_EXPLICIT_BOUND"
    assert issue["diagnostic_code"] == "CUSTOM_PROVIDER_DESTINATION_REQUIRES_REVIEW"
    assert "Metadata observation" in issue["next_action"]
    assert "owned.invalid" not in json.dumps(state)
