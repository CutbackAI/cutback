"""Actual CLI, native SDK campaign, PostgreSQL rights and automatic checked source handoff."""

import json

import pytest
import support_agent
from test_campaign_operator_local import setup_local
from typer.testing import CliRunner

import cutback.agent_campaign_handoff as handoff
from cutback.agent import load_session
from cutback.application_checks import git, snapshot
from cutback.cli import app
from cutback.core.workspace import read_private

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("revoke_after_checks", [False, True])
def test_native_campaign_cli_finish_refreshes_rights_without_repeating_native_spend(
    api, tenant, tmp_path, monkeypatch, postgres_url, revoke_after_checks
):
    fresh = support_agent.fresh_application

    def committed_application(root, *args, **options):
        fresh(root, *args, **options)
        (root / ".gitignore").write_text(".cutback/\n__pycache__/\n", encoding="utf-8")
        git(root, "init", "--template=")
        git(root, "add", ".")
        git(
            root,
            "-c",
            "user.name=Synthetic",
            "-c",
            "user.email=synthetic@example.invalid",
            "commit",
            "-m",
            "Independent committed application",
        )

    monkeypatch.setattr(support_agent, "fresh_application", committed_application)
    root, control, _, _, calls = setup_local(api, tenant, tmp_path, monkeypatch, postgres_url)
    runner = CliRunner()
    run = runner.invoke(app, ["campaign", "run", "--target", str(root)])
    assert run.exit_code == 0, run.output
    original, native_count = snapshot(root), sum(len(group) for group in calls)
    commands = tmp_path / "owned-commands.json"
    commands.write_text(
        json.dumps(
            [
                {
                    "key": "owned-source-compile",
                    "arguments": [
                        "-c",
                        "from pathlib import Path; "
                        "compile(Path('app.py').read_text(), 'app.py', 'exec')",
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    destination = tmp_path / "private-handoff"
    arguments = [
        "campaign",
        "finish",
        "--target",
        str(root),
        "--workspace",
        str(destination),
        "--purpose-reference",
        "owned-sample-handoff",
        "--sample-only",
        "--commands",
        str(commands),
    ]
    preview = runner.invoke(app, arguments)
    assert preview.exit_code == 0, preview.output
    value = json.loads(preview.stdout)
    assert value["status"] == "CHECKS_READY" and not value["application_checks_executed"]
    coverage = tmp_path / "owned-coverage.json"
    coverage.write_text(
        json.dumps({key: value["command_keys"] for key in value["affected_workflows"]}),
        encoding="utf-8",
    )
    actual, physical_checks = handoff.run_checks, []

    def checked(*args, **kwargs):
        physical_checks.append(True)
        result = actual(*args, **kwargs)
        if revoke_after_checks:
            response = api[0].delete(
                f"/v1/projects/{tenant[0]}/capture-policies/{load_session(root)['policy_id']}",
                headers=api[1],
            )
            assert response.status_code == 200
        return result

    monkeypatch.setattr(handoff, "run_checks", checked)
    execute = [*arguments, "--execute-local", "--coverage", str(coverage)]
    result = runner.invoke(app, execute)
    if revoke_after_checks:
        assert result.exit_code != 0
        assert not (destination / "review.json").exists()
    else:
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["status"] == "REVIEWED"
        assert runner.invoke(app, execute).exit_code == 0
    assert physical_checks == [True]
    assert read_private(destination / "checks.json")["all_passed"]
    assert sum(len(group) for group in calls) == native_count
    assert snapshot(root) == original
    assert (
        control.token not in result.output
        and "Owned synthetic classification task" not in result.output
    )
