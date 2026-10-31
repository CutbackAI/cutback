"""Separate scenario actions, stable identity and private destination before content reads."""

import json
from uuid import uuid4

import typer
from typer.testing import CliRunner

from cutback.cli_scenarios import register_scenario


def test_scenario_cli_uses_scoped_actions_and_protected_content_output(tmp_path):
    calls, outputs = [], []
    app = typer.Typer()

    def request(*args, **kwargs):
        calls.append((args, kwargs))
        return {"state": "OWNED_SYNTHETIC_RESPONSE"}

    def fail(*args):
        raise typer.Exit(2)

    register_scenario(app, lambda value, path=None: outputs.append(value), request, fail)
    project, scenario = str(uuid4()), str(uuid4())
    destination = tmp_path / "private" / "response.json"
    common = [
        "--project",
        project,
        "--endpoint",
        "http://127.0.0.1:8080",
        "--output",
        str(destination),
    ]
    body = tmp_path / "request.json"
    body.write_text(
        json.dumps({"dataset_id": str(uuid4()), "request_id": str(uuid4())}), encoding="utf-8"
    )
    actions = [
        (["draft", str(body)], "POST", "/drafts"),
        (["show", scenario], "GET", "/" + scenario),
        (
            ["approve-generation", scenario, "a" * 64, "synthetic-budget"],
            "POST",
            "/" + scenario + "/approve-generation",
        ),
        (["queue", scenario, "a" * 64], "POST", "/" + scenario + "/runs"),
        (
            ["review", scenario, "b" * 64, "approve", "synthetic-reviewed"],
            "POST",
            "/" + scenario + "/review",
        ),
        (["import", scenario, str(body)], "POST", "/" + scenario + "/imports"),
        (["content", scenario, "synthetic-private-review"], "POST", "/" + scenario + "/content"),
    ]
    for command, method, suffix in actions:
        result = CliRunner().invoke(app, ["scenarios", *command, *common])
        assert result.exit_code == 0, result.output
        assert calls[-1][0] == (
            method,
            "http://127.0.0.1:8080",
            f"/v1/projects/{project}/scenarios" + suffix,
        )
    assert len(calls) == len(actions) and len(outputs) == len(actions) - 1
    assert json.loads(destination.read_text("utf-8")) == {"state": "OWNED_SYNTHETIC_RESPONSE"}
    bad = CliRunner().invoke(
        app, ["scenarios", "content", scenario, "synthetic", *common[:-1], str(tmp_path)]
    )
    assert bad.exit_code == 2 and len(calls) == len(actions)
    body.write_text(json.dumps({"dataset_id": str(uuid4())}), encoding="utf-8")
    assert CliRunner().invoke(app, ["scenarios", "draft", str(body), *common]).exit_code == 2
    assert len(calls) == len(actions)


def test_private_output_rejects_redirected_parent_before_request(tmp_path):
    from cutback.core.workspace import prepare_private_output

    target = tmp_path / "existing"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        import pytest

        pytest.skip("host disallows creating directory symlinks")
    import pytest

    with pytest.raises(ValueError, match="UNSAFE_PRIVATE_OUTPUT_DIRECTORY"):
        prepare_private_output(link / "private.json")
