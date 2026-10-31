"""Core CLI keeps one-shot analysis identities and never silently retries storage work."""

import json
from uuid import uuid4

import typer
from typer.testing import CliRunner

from cutback.cli_native_profiles import register_native_profiles


def test_request_profile_cli_preserves_exact_identity_and_does_not_retry_uncertain_post(tmp_path):
    app, calls, output = typer.Typer(), [], []
    body = {"request_id": str(uuid4()), "purpose": "owned-test"}
    path = tmp_path / "analysis.json"
    path.write_text(json.dumps(body), encoding="utf-8")

    def request(*args, **kwargs):
        calls.append((args, kwargs))
        raise ValueError("UNCERTAIN_OWNED_SUBMISSION")

    register_native_profiles(app, output.append, request, lambda *args: None)
    result = CliRunner().invoke(app, ["request-profiles", "analyze", str(uuid4()), str(path)])
    assert result.exit_code != 0
    assert len(calls) == 1 and calls[0][1]["json"] == body
    assert not output


def test_request_profile_cli_rejects_invalid_configuration_before_network(tmp_path):
    app, calls, errors = typer.Typer(), [], []

    def fail(code, action):
        errors.append(code)
        raise typer.Exit(2)

    register_native_profiles(app, lambda value: None, lambda *a, **k: calls.append(1), fail)
    source = tmp_path / "input.json"
    args = ["request-profiles", "analyze", str(uuid4()), str(source)]
    runner = CliRunner()
    for content in ("[]", "{}", '{"request_id":"bad"}', "x" * 1048577):
        source.write_text(content, encoding="utf-8")
        assert runner.invoke(app, args).exit_code == 2
    assert not calls and len(errors) == 4


def test_request_profile_cli_list_show_cancel_have_scoped_explicit_methods():
    app, calls = typer.Typer(), []
    register_native_profiles(
        app,
        lambda value: None,
        lambda *args, **kwargs: calls.append((args, kwargs)),
        lambda *args: None,
    )
    project, analysis = str(uuid4()), str(uuid4())
    runner = CliRunner()
    for command in ("list", "show", "cancel"):
        args = ["request-profiles", command, project]
        if command != "list":
            args.append(analysis)
        assert runner.invoke(app, args).exit_code == 0
    assert [call[0][0] for call in calls] == ["GET", "GET", "POST"]
    assert all(
        f"/projects/{project}/native-request-profiles/analyses" in call[0][2] for call in calls
    )
    assert calls[-1][0][2].endswith(f"/{analysis}/cancel")
