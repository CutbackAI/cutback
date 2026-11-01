"""The container deadline survives loss of the complete owned host process tree."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from support_application_container import container_image as container_image
from test_application_checks import repository

import cutback
from cutback.application_checks import (
    ApplicationCommand,
    WindowsJob,
    prepare,
    stop_process,
)
from cutback.application_container import LocalDocker, isolate


def test_daemon_deadline_stops_detached_work_after_host_runner_tree_is_killed(
    tmp_path, container_image
):
    base = repository(tmp_path / "base", "pass\n")
    proposed = repository(tmp_path / "proposed", "pass\n")
    command = ApplicationCommand(
        key="crash-bounded",
        timeout_seconds=8,
        arguments=[
            "-c",
            "import signal,subprocess,sys,time; "
            "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            "subprocess.Popen([sys.executable,'-c',"
            "'import time; time.sleep(300)'],start_new_session=True); "
            "time.sleep(300)",
        ],
    )
    contract = isolate(prepare(base, proposed, [command]), container_image)
    contract_file, marker = tmp_path / "contract.json", tmp_path / "owned-container.json"
    contract_file.write_text(json.dumps(contract.model_dump(mode="json")))
    temporary = tmp_path / "private-runner-temp"
    temporary.mkdir()
    # This helper has exactly the outer test's source/installed package. It
    # does not receive provider/admin variables or expose application output.
    code = (
        "import json,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
        "import cutback.application_container as c; "
        "from cutback.application_checks import validate_contract,run_checks; "
        "real=c.create\n"
        "def record(client,container,source,command,name,*,probe=False,contract_hash):\n"
        " identifier=real(client,container,source,command,name,probe=probe,"
        "contract_hash=contract_hash)\n"
        " if not probe:\n"
        "  ready=Path(sys.argv[5]+'.ready'); ready.write_text(json.dumps({'id':identifier})); "
        "ready.replace(sys.argv[5])\n"
        " return identifier\n"
        "c.create=record\n"
        "run_checks(validate_contract(json.loads(Path(sys.argv[2]).read_text())),"
        "Path(sys.argv[3]),Path(sys.argv[4]),execute_local=True)"
    )
    env = {
        key: os.environ[key]
        for key in (
            "SYSTEMROOT",
            "WINDIR",
            "USERPROFILE",
            "HOME",
            "PATH",
            "LOCALAPPDATA",
            "APPDATA",
        )
        if key in os.environ
    }
    env.update({name: str(temporary) for name in ("TEMP", "TMP", "TMPDIR")})
    client, identifier = LocalDocker(), None
    job = WindowsJob() if os.name == "nt" else None
    process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-c",
            code,
            str(Path(cutback.__file__).resolve().parents[1]),
            str(contract_file),
            str(base),
            str(proposed),
            str(marker),
        ],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=os.name != "nt",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW | 0x4
        if os.name == "nt"
        else 0,
    )
    try:
        if job:
            job.assign_and_resume(process)
        deadline, running = time.monotonic() + 30, False
        while time.monotonic() < deadline:
            if marker.is_file():
                identifier = json.loads(marker.read_text())["id"]
                state = json.loads(
                    client.call("inspect", "--format", "{{json .State}}", identifier)
                )
                if state["Running"]:
                    running = True
                    break
            assert process.poll() is None, "OWNED_RUNNER_STOPPED_BEFORE_CRASH_INJECTION"
            time.sleep(0.05)
        assert running, "OWNED_CONTAINER_DID_NOT_START"
        # Kill the owner and attached Docker CLI, without requesting daemon kill.
        stop_process(process, job)
        job = None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            state = json.loads(client.call("inspect", "--format", "{{json .State}}", identifier))
            if not state["Running"]:
                break
            time.sleep(0.1)
        assert state["Running"] is False, "CONTAINER_OUTLIVED_ITS_INDEPENDENT_DEADLINE"
        assert state["ExitCode"] in (124, 137)
    finally:
        if process.poll() is None or job:
            stop_process(process, job)
        if identifier:
            client.call("rm", "--force", identifier)
