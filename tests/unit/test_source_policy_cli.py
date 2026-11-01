"""Public controls preserve approval boundaries and omit private validation details."""

import subprocess
import sys
from uuid import uuid4

import pytest
import typer
from typer.testing import CliRunner

from cutback.cli_policy_program import register_policy_program


def command():
    app, result, errors = typer.Typer(), [], []

    def fail(code, action):
        errors.append((code, action))
        raise typer.Exit(2)

    register_policy_program(app, result.append, fail)
    return app, result, errors


@pytest.mark.parametrize("async_consumer", [False, True])
def test_independent_sample_copies_and_runs_actual_native_consumer_without_paid_io(
    tmp_path, async_consumer
):
    app, output, errors = command()
    root = tmp_path / "independent-policy-sample"
    args = ["source-policy", "sample", str(root), *(["--async"] if async_consumer else [])]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert output == [{"status": "SYNTHETIC_SAMPLE_READY", "provider_calls": 0}] and errors == []
    checked = subprocess.run(
        [sys.executable, "check_application.py"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "contract passed" in checked.stdout


def test_preparation_forwards_explicit_scope_and_budget_without_auto_approval(
    tmp_path, monkeypatch
):
    import cutback.agent_control as connection
    import cutback.agent_policy_program as program

    app, output, errors = command()
    control, project, calls = object(), str(uuid4()), []
    monkeypatch.setattr(connection, "connect", lambda *a, **kw: (control, project))

    def prepare(root, remote, owner, **kwargs):
        calls.append((root, remote, owner, kwargs))
        return {"status": "READY_FOR_APPROVAL"}

    monkeypatch.setattr(program, "prepare", prepare)
    monkeypatch.setattr(
        program, "advance", lambda *a, **kw: (_ for _ in ()).throw(AssertionError())
    )
    dataset, snapshot = str(uuid4()), str(uuid4())
    result = CliRunner().invoke(
        app,
        [
            "source-policy",
            "prepare",
            "--target",
            str(tmp_path),
            "--dataset",
            dataset,
            "--baseline-snapshot",
            snapshot,
            "--producer",
            "app.py",
            "--purpose-reference",
            "owned-cli-fixture",
            "--max-usd",
            "0.012345678901",
            "--no-generate",
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(calls) == 1 and calls[0][1:3] == (control, project)
    assert calls[0][3]["max_usd"] == "0.012345678901"
    assert calls[0][3]["dataset_id"] == dataset
    assert calls[0][3]["baseline_snapshot_id"] == snapshot
    assert calls[0][3]["generate"] is False
    assert output == [{"status": "READY_FOR_APPROVAL"}] and not errors


def test_uncertain_approval_is_not_repeated_and_content_errors_are_redacted(tmp_path, monkeypatch):
    import cutback.agent_control as connection
    import cutback.agent_policy_program as program

    app, output, errors = command()
    calls = []
    monkeypatch.setattr(connection, "connect", lambda *a, **kw: (object(), str(uuid4())))

    def approve(*args, **kwargs):
        calls.append(kwargs)
        raise ValueError("PRIVATE_SYNTHETIC_INSTRUCTION: do not print this")

    monkeypatch.setattr(program, "advance", approve)
    result = CliRunner().invoke(
        app,
        [
            "source-policy",
            "continue",
            "--target",
            str(tmp_path),
            "--program-hash",
            "a" * 64,
            "--approval-reference",
            "owned-cli-approval",
        ],
    )
    assert result.exit_code == 2 and len(calls) == 1
    assert calls[0] == {"program_hash": "a" * 64, "approval_ref": "owned-cli-approval"}
    assert "PRIVATE_SYNTHETIC" not in result.output + str(errors)
    assert errors[0][0] == "POLICY_PROGRAM_ACTION_STOPPED" and not output


@pytest.mark.parametrize(
    "current_routed,async_consumer,code",
    [
        (False, True, "ASYNC_COLLECTOR_UPGRADE_REQUIRED"),
        (True, False, "CURRENT_ROUTER_COLLECTOR_UPGRADE_REQUIRED"),
    ],
)
def test_older_collectors_get_a_concrete_upgrade_action_before_any_payload_upload(
    tmp_path, monkeypatch, current_routed, async_consumer, code
):
    from support_policy_application import application

    from cutback.agent_policy_program import prepare

    root = application(tmp_path, current_routed=current_routed, async_consumer=async_consumer)
    calls = []

    class OldCollector:
        endpoint = "https://collector.example.invalid"

        def __call__(self, method, path):
            calls.append((method, path))
            assert method == "GET" and path == "/v1/health/ready"
            return {"source_policy_program_contract": "cutback.source-policy-program.v1"}

    with pytest.raises(ValueError, match=code):
        prepare(
            root,
            OldCollector(),
            uuid4(),
            dataset_id=uuid4(),
            baseline_snapshot_id=uuid4(),
            producer_file="app.py",
            purpose="owned-upgrade-check",
            max_usd="1",
            generate=False,
        )
    assert calls == [("GET", "/v1/health/ready")]
    assert not (root / ".cutback/agent/policy-program.json").exists()
    import cutback.agent_control as connection

    monkeypatch.setattr(connection, "connect", lambda *a, **kw: (OldCollector(), str(uuid4())))
    app, output, errors = command()
    result = CliRunner().invoke(
        app,
        [
            "source-policy",
            "prepare",
            "--target",
            str(root),
            "--dataset",
            str(uuid4()),
            "--baseline-snapshot",
            str(uuid4()),
            "--producer",
            "app.py",
            "--purpose-reference",
            "owned-upgrade-check",
            "--max-usd",
            "1",
            "--no-generate",
        ],
    )
    assert result.exit_code == 2 and not output
    assert errors[0][0].endswith(code)
    assert "Upgrade the collector and worker" in errors[0][1]
    assert "SDK pin unchanged" in errors[0][1]
    assert not (root / ".cutback/agent/policy-program.json").exists()
