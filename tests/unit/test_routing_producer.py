"""Source qualification parses the whole bounded consumer without importing it."""

import pytest

from cutback.core.routing_producer import PROGRAM, PROGRAM_SHA256, RoutingSourceBinding, qualify


def binding(**changes):
    return RoutingSourceBinding(
        **{
            "producer_file": "src/app.py",
            "producer_sha256": "a" * 64,
            "settings_file": "src/routing.json",
            "settings_sha256": "b" * 64,
            "source_tree_hash": "c" * 64,
            "commit": "d" * 40,
            "configuration_key": "owned-router",
            "configuration_model": "input-size-v1",
            "policy_sha256": "e" * 64,
            "baseline_sha256": "f" * 64,
            "max_input_bytes": 4000,
            **changes,
        }
    )


def test_exact_program_and_owned_comment_or_docstring_qualify_parse_only():
    assert qualify(PROGRAM.encode()) == PROGRAM_SHA256
    assert (
        qualify(('"""Independent application."""\n# Comment\n' + PROGRAM).encode())
        == PROGRAM_SHA256
    )
    assert binding().program_sha256 == PROGRAM_SHA256


@pytest.mark.parametrize(
    "source",
    [
        PROGRAM.replace("input=text", 'input="changed " + text'),
        PROGRAM.replace("store=False", "store=True"),
        PROGRAM.replace(
            "return client.responses.create(", "return client.responses.create(stream=True,"
        ),
        PROGRAM.replace("type(client) is not OpenAI", "False"),
        PROGRAM.replace("route_target(policy, text)", "route_target(policy, text.strip())"),
        PROGRAM + '\nraise RuntimeError("must never execute source")\n',
    ],
)
def test_changed_frame_retry_endpoint_or_extra_code_refuses(source):
    with pytest.raises(ValueError, match="NOT_QUALIFIED"):
        qualify(source.encode())


@pytest.mark.parametrize(
    "changes",
    [
        {"producer_file": "../app.py"},
        {"settings_file": "src/other.json"},
        {"program_sha256": "0" * 64},
        {"max_input_bytes": True},
    ],
)
def test_binding_rejects_traversal_unpaired_files_or_changed_contract(changes):
    with pytest.raises(ValueError):
        binding(**changes)
