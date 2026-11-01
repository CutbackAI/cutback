"""Versioned contracts and actual local Docker containment; synthetic applications only."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from support_application_container import container_image as container_image
from support_application_container import container_node_image as container_node_image
from test_application_checks import repository
from typer.testing import CliRunner

from cutback.application_checks import (
    ApplicationCommand,
    prepare,
    run_checks,
    validate_contract,
    validate_receipt,
    verify,
)
from cutback.application_container import ApplicationContainer, LocalDocker, isolate
from cutback.cli import app
from cutback.core.prompts import fingerprint


@pytest.fixture
def owned_container_names(monkeypatch):
    names, original = [], LocalDocker.call

    def record(self, *args, **kwargs):
        if args[0] == "create":
            names.append(args[args.index("--name") + 1])
        return original(self, *args, **kwargs)

    monkeypatch.setattr(LocalDocker, "call", record)
    yield names
    # Other accepted test cohorts may use the same daemon concurrently. Verify
    # only this invocation's exact random identities, not a global name listing.
    client = LocalDocker()
    assert names
    for name in names:
        assert not client.call("ps", "-aq", "--filter", "name=^/" + name + "$").strip()


def test_v1_contract_and_receipt_identities_match_the_qualified_pre_container_wheel():
    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/application-check-v1-compatibility.json").read_text()
    )
    raw, receipt = fixture["contract"], fixture["receipt"]
    assert validate_contract(raw).model_dump(mode="json") == raw
    assert fingerprint(raw) == receipt["contract_hash"]
    validate_receipt(receipt)
    assert (
        fingerprint({key: value for key, value in receipt.items() if key != "receipt_hash"})
        == (receipt["receipt_hash"])
    )
    assert (
        receipt["contract_hash"]
        == "e9dc94e6ede9432c0f088a1a68bfc7a22adaae5a10f32246e9216514e573f2c5"
    )
    assert (
        receipt["receipt_hash"]
        == "4943fe687bed4b05b8ac3e8a9102ecdf04e0fa3b36e35f9a1ed635056d6306a8"
    )


@pytest.mark.parametrize(
    "image", ["python:latest", "python@sha256:" + "a" * 64, "sha256:" + "A" * 64]
)
def test_image_must_be_an_exact_local_content_identifier(image):
    with pytest.raises(ValidationError):
        ApplicationContainer(image_id=image)


@pytest.mark.parametrize(
    "limits",
    [
        {"memory_mb": 127},
        {"memory_mb": True},
        {"pids": 500},
        {"tmp_mb": 512},
        {"network": "host"},
        {"privileged": True},
    ],
)
def test_contract_cannot_enable_network_privilege_or_unbounded_resources(limits):
    with pytest.raises(ValidationError):
        ApplicationContainer(image_id="sha256:" + "a" * 64, **limits)


def test_isolation_preparation_does_not_execute_or_contact_docker(tmp_path, monkeypatch):
    base = repository(tmp_path / "base", "print('base')\n")
    proposed = repository(tmp_path / "proposed", "print('proposed')\n")
    contract = prepare(base, proposed, [ApplicationCommand(key="check", arguments=["sample.py"])])
    old = contract.model_dump(mode="json")
    monkeypatch.setattr(LocalDocker, "__init__", lambda *_: pytest.fail("preparation ran Docker"))
    isolated = isolate(contract, {"image_id": "sha256:" + "a" * 64})
    assert contract.model_dump(mode="json") == old
    assert old["schema_version"] == "cutback.application-check.v1" and "container" not in old
    assert isolated.schema_version == "cutback.application-check.v2"
    assert validate_contract(isolated.model_dump(mode="json")) == isolated
    with pytest.raises(ValueError, match="EXPLICIT_LOCAL"):
        run_checks(isolated, base, proposed)
    source, destination = tmp_path / "local.json", tmp_path / "isolated.json"
    source.write_text(json.dumps(old))
    result = CliRunner().invoke(
        app,
        [
            "checks",
            "isolate",
            str(source),
            "--image-id",
            "sha256:" + "a" * 64,
            "--output",
            str(destination),
        ],
    )
    assert result.exit_code == 0, result.output
    assert validate_contract(json.loads(destination.read_text())) == isolated
    assert json.loads(source.read_text()) == old


def test_remote_daemon_is_rejected_before_image_or_application_access(monkeypatch):
    calls = []

    def remote(self, *arguments, **options):
        calls.append(arguments)
        return json.dumps("tcp://example.invalid:2376")

    monkeypatch.setattr("cutback.application_container.shutil.which", lambda _: "docker")
    monkeypatch.setattr(LocalDocker, "call", remote)
    with pytest.raises(ValueError, match="LOCAL_DAEMON_REQUIRED"):
        LocalDocker()
    assert len(calls) == 1 and calls[0][:2] == ("context", "inspect")


@pytest.mark.parametrize("unsupported", ["os", "memory", "pids", "seccomp"])
def test_unsupported_daemon_limits_do_not_fall_back_to_host_execution(monkeypatch, unsupported):
    info = {
        "OSType": "linux",
        "MemoryLimit": True,
        "PidsLimit": True,
        "SecurityOptions": ["name=seccomp,profile=builtin"],
    }
    info.update(
        {
            "os": {"OSType": "windows"},
            "memory": {"MemoryLimit": False},
            "pids": {"PidsLimit": False},
            "seccomp": {"SecurityOptions": []},
        }[unsupported]
    )

    def response(self, *args, **kwargs):
        return json.dumps("unix:///var/run/docker.sock" if args[0] == "context" else info)

    monkeypatch.setattr("cutback.application_container.shutil.which", lambda _: "docker")
    monkeypatch.setattr(LocalDocker, "call", response)
    with pytest.raises(ValueError, match="LINUX_LIMITS_AND_SECCOMP_REQUIRED"):
        LocalDocker()


@pytest.mark.parametrize(
    "path", ["python", "/usr/../bin/python", "/tmp/python;echo", "/bin/./python"]
)
def test_runtime_is_a_bounded_absolute_image_path(path):
    with pytest.raises(ValueError, match="ABSOLUTE_RUNTIME_PATH_REQUIRED"):
        ApplicationContainer(image_id="sha256:" + "a" * 64, python_path=path)


def test_actual_node_container_binds_source_and_discards_application_output(
    tmp_path, monkeypatch, container_node_image
):
    base = repository(tmp_path / "base", "print('base')\n")
    proposed = repository(tmp_path / "proposed", "print('proposed')\n")
    monkeypatch.setenv("NODE_OPTIONS", "--require=PRIVATE_AMBIENT_MODULE")
    monkeypatch.setenv("OPENAI_API_KEY", "PRIVATE_AMBIENT_KEY")
    command = ApplicationCommand(
        key="node-container",
        runtime="node",
        arguments=[
            "-e",
            "const f=require('fs');"
            "if(process.getuid()!==10001 || process.env.OPENAI_API_KEY || "
            "process.env.NODE_OPTIONS || f.existsSync('.git'))process.exit(9);"
            "if(!f.readFileSync('sample.py','utf8').includes('print'))process.exit(8);"
            "process.stdout.write('PRIVATE_NODE_OUTPUT')",
        ],
    )
    contract = isolate(prepare(base, proposed, [command]), container_node_image)
    receipt = run_checks(contract, base, proposed, execute_local=True)
    assert receipt["all_passed"] and "PRIVATE_" not in json.dumps(receipt)
    assert verify(contract, receipt, base, proposed)["authenticity"] == "not_independently_attested"


def test_daemon_configuration_mismatch_is_detected_before_starting_application(
    tmp_path, monkeypatch, container_image
):
    base = repository(tmp_path / "base", "pass\n")
    proposed = repository(tmp_path / "proposed", "pass\n")
    command = ApplicationCommand(key="check", arguments=["sample.py"])
    contract = isolate(prepare(base, proposed, [command]), container_image)
    real, starts = LocalDocker.call, []

    def changed(self, *args, **kwargs):
        if args[0] == "start":
            starts.append(True)
        result = real(self, *args, **kwargs)
        if args[0] == "inspect" and len(args) == 2:
            value = json.loads(result)
            value[0]["HostConfig"]["NetworkMode"] = "host"
            return json.dumps(value)
        return result

    monkeypatch.setattr(LocalDocker, "call", changed)
    with pytest.raises(ValueError, match="ISOLATION_NOT_ENFORCED"):
        run_checks(contract, base, proposed, execute_local=True)
    assert starts == []


def test_actual_container_has_no_host_secrets_git_ignored_files_or_network(
    tmp_path, monkeypatch, container_image
):
    base = repository(tmp_path / "base", "print('PRIVATE_BASE_OUTPUT')\n")
    proposed = repository(tmp_path / "proposed", "print('PRIVATE_PROPOSED_OUTPUT')\n")
    # Both ignored files and Git history must be absent from the only source mount.
    for root in (base, proposed):
        (root / ".git" / "info" / "exclude").write_text("ignored.txt\n")
        (root / "ignored.txt").write_text("PRIVATE_IGNORED_SECRET")
    monkeypatch.setenv("OPENAI_API_KEY", "PRIVATE_AMBIENT_PROVIDER_KEY")
    monkeypatch.setenv("CUTBACK_CONTROL_TOKEN", "PRIVATE_AMBIENT_ADMIN_KEY")
    source = (
        "import os,pathlib,socket; assert os.getuid()==10001; "
        "assert not any(k in os.environ for k in ('OPENAI_API_KEY','CUTBACK_CONTROL_TOKEN')); "
        "assert not pathlib.Path('.git').exists(); "
        "assert not pathlib.Path('ignored.txt').exists(); "
        "assert not pathlib.Path('/var/run/docker.sock').exists(); "
        "pathlib.Path('/tmp/allowed').write_text('fixture'); "
        "\nfor path in ('sample.py','/root-not-writable'):\n"
        " try: pathlib.Path(path).write_text('forbidden')\n"
        " except OSError: pass\n"
        " else: raise AssertionError('write was permitted')\n"
        "s=socket.socket(); s.settimeout(.2)\n"
        "try: s.connect(('203.0.113.1',443))\n"
        "except OSError: pass\n"
        "else: raise AssertionError('network was permitted')\n"
        "finally: s.close()\n"
        "print('PRIVATE_CHECK_OUTPUT')"
    )
    commands = [
        ApplicationCommand(key="isolation", arguments=["-c", source]),
        ApplicationCommand(key="application", arguments=["sample.py"]),
    ]
    contract = isolate(prepare(base, proposed, commands), container_image)
    receipt = run_checks(contract, base, proposed, execute_local=True)
    assert receipt["all_passed"] and len(receipt["results"]) == 4
    assert receipt["isolation"] == "local_linux_container_network_none"
    assert "PRIVATE_" not in json.dumps(receipt)
    assert verify(contract, receipt, base, proposed)["status"] == "PASSED_RECORDED_LOCAL_CHECKS"
    assert validate_receipt(receipt).container == container_image
    altered = {**receipt, "container": {**receipt["container"], "pids": 32}}
    altered["receipt_hash"] = fingerprint(
        {key: value for key, value in altered.items() if key != "receipt_hash"}
    )
    with pytest.raises(ValueError, match="RECEIPT_OR_SOURCE"):
        verify(contract, altered, base, proposed)
    plain = prepare(base, proposed, commands)
    with pytest.raises(ValueError, match="RECEIPT_OR_SOURCE"):
        verify(plain, receipt, base, proposed)


@pytest.mark.parametrize("mode", ["exit", "timeout", "output"])
def test_actual_container_failure_limits_and_descendants_are_removed(
    tmp_path, container_image, mode, owned_container_names
):
    base = repository(tmp_path / "base", "pass\n")
    proposed = repository(tmp_path / "proposed", "pass\n")
    source = {
        "exit": "raise SystemExit(7)",
        "output": "print('PRIVATE_OUTPUT'*100000)",
        "timeout": "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(300)'],"
        "start_new_session=True); time.sleep(300)",
    }[mode]
    command = ApplicationCommand(
        key="bounded",
        arguments=["-c", source],
        timeout_seconds=1 if mode == "timeout" else 30,
        max_output_bytes=1024,
    )
    contract = isolate(prepare(base, proposed, [command]), container_image)
    receipt = run_checks(contract, base, proposed, execute_local=True)
    assert len(owned_container_names) == 4 and not receipt["all_passed"]
    expected = {"exit": "failed", "timeout": "timeout", "output": "output_limit"}[mode]
    assert [result["status"] for result in receipt["results"]] == [expected, expected]
    assert "PRIVATE_OUTPUT" not in json.dumps(receipt)
    with pytest.raises(ValueError, match="NOT_ALL_PASSED"):
        verify(contract, receipt, base, proposed)


def test_container_cleanup_runs_when_application_attachment_raises(
    tmp_path, monkeypatch, container_image, owned_container_names
):
    base = repository(tmp_path / "base", "pass\n")
    proposed = repository(tmp_path / "proposed", "pass\n")
    contract = isolate(
        prepare(base, proposed, [ApplicationCommand(key="bounded", arguments=["sample.py"])]),
        container_image,
    )

    def lost(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr("cutback.application_container.bounded_start", lost)
    with pytest.raises(KeyboardInterrupt):
        run_checks(contract, base, proposed, execute_local=True)
    assert len(owned_container_names) == 2


def test_actual_pid_and_temporary_storage_limits_are_enforced(tmp_path, container_image):
    base = repository(tmp_path / "base", "pass\n")
    proposed = repository(tmp_path / "proposed", "pass\n")
    source = (
        "import errno,subprocess\nchildren=[]\n"
        "try:\n"
        " for _ in range(32):\n"
        "  try: children.append(subprocess.Popen(['/bin/sleep','20']))\n"
        "  except OSError as error:\n"
        "   assert error.errno==errno.EAGAIN; break\n"
        " assert 0<len(children)<16\n"
        "finally:\n"
        " for child in children: child.terminate()\n"
        " for child in children: child.wait()\n"
        "try:\n"
        " with open('/tmp/limited','wb') as file: file.write(b'x'*(9*1024*1024))\n"
        "except OSError as error: assert error.errno==errno.ENOSPC\n"
        "else: raise AssertionError('temporary storage bound not enforced')\n"
    )
    bounded = container_image.model_copy(update={"pids": 16, "tmp_mb": 8})
    contract = isolate(
        prepare(
            base,
            proposed,
            [
                ApplicationCommand(key="actual-limits", arguments=["-c", source]),
            ],
        ),
        bounded,
    )
    receipt = run_checks(contract, base, proposed, execute_local=True)
    assert receipt["all_passed"]
