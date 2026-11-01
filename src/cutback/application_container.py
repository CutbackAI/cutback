"""Opt-in local Linux containers for exact source checks; no image pulls or builds.

The Docker daemon and reviewed image are trusted. Containers are not a VM or an
independent attestation service. Application output never becomes a receipt.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field, field_validator

from cutback.application_checks import (
    ApplicationCheckFields,
    ApplicationCheckReceiptFields,
    snapshot,
    source_root,
)
from cutback.core.models import StrictModel
from cutback.core.prompts import fingerprint


class ApplicationContainer(StrictModel):
    image_id: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    memory_mb: int = Field(default=512, strict=True, ge=128, le=4096)
    pids: int = Field(default=64, strict=True, ge=16, le=256)
    tmp_mb: int = Field(default=64, strict=True, ge=8, le=256)
    python_path: str = "/usr/local/bin/python"
    node_path: str = "/usr/local/bin/node"

    @field_validator("python_path", "node_path")
    @classmethod
    def runtime_path(cls, value):
        if (
            not re.fullmatch(r"/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+", value)
            or any(part in {".", ".."} for part in value.split("/"))
            or len(value) > 256
        ):
            raise ValueError("APPLICATION_CONTAINER_ABSOLUTE_RUNTIME_PATH_REQUIRED")
        return value


class ContainerCheckContract(ApplicationCheckFields):
    schema_version: Literal["cutback.application-check.v2"] = "cutback.application-check.v2"
    container: ApplicationContainer


class ContainerCheckReceipt(ApplicationCheckReceiptFields):
    schema_version: Literal["cutback.application-check-receipt.v2"]
    isolation: Literal["local_linux_container_network_none"]
    container: ApplicationContainer


def isolate(contract, container):
    """Convert a reviewed v1 contract without discovery, code execution or image pull."""
    return ContainerCheckContract.model_validate(
        {
            **contract.model_dump(mode="json"),
            "schema_version": "cutback.application-check.v2",
            "container": ApplicationContainer.model_validate(container).model_dump(mode="json"),
        }
    )


class LocalDocker:
    def __init__(self):
        self.binary = shutil.which("docker")
        if not self.binary:
            raise ValueError("APPLICATION_CONTAINER_LOCAL_DOCKER_REQUIRED")
        # Only the trusted CLI sees host configuration. Nothing in this environment
        # is forwarded to a container; DOCKER_HOST/CONTEXT/TLS overrides are excluded.
        self.env = {
            key: os.environ[key]
            for key in (
                "SYSTEMROOT",
                "WINDIR",
                "USERPROFILE",
                "HOME",
                "PATH",
                "LOCALAPPDATA",
                "APPDATA",
                "TEMP",
                "TMP",
            )
            if key in os.environ
        }
        self.endpoint = None
        endpoint = json.loads(
            self.call("context", "inspect", "--format", "{{json .Endpoints.docker.Host}}")
        )
        if (
            not isinstance(endpoint, str)
            or not (endpoint.startswith("unix:///") or endpoint.startswith("npipe:////./pipe/"))
            or any(char in endpoint for char in ("\r", "\n", "\0"))
        ):
            raise ValueError("APPLICATION_CONTAINER_LOCAL_DAEMON_REQUIRED")
        self.endpoint = endpoint
        info = json.loads(self.call("info", "--format", "{{json .}}"))
        if (
            info.get("OSType") != "linux"
            or info.get("MemoryLimit") is not True
            or info.get("PidsLimit") is not True
            or "name=seccomp,profile=builtin" not in info.get("SecurityOptions", [])
        ):
            raise ValueError("APPLICATION_CONTAINER_LINUX_LIMITS_AND_SECCOMP_REQUIRED")

    def argv(self, *args):
        return [self.binary, *(["--host", self.endpoint] if self.endpoint else []), *args]

    def call(self, *args, timeout=30):
        try:
            result = subprocess.run(
                self.argv(*args),
                env=self.env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError("APPLICATION_CONTAINER_CONTROL_UNAVAILABLE") from None
        if result.returncode or len(result.stdout) > 1048576:
            raise ValueError("APPLICATION_CONTAINER_CONTROL_FAILED")
        return result.stdout.decode("utf-8")

    def image(self, expected):
        images = json.loads(self.call("image", "inspect", expected))
        if len(images) != 1 or images[0].get("Id") != expected:
            raise ValueError("APPLICATION_CONTAINER_EXACT_LOCAL_IMAGE_REQUIRED")
        config = images[0].get("Config", {})
        if images[0].get("Os") != "linux" or config.get("Volumes"):
            raise ValueError("APPLICATION_CONTAINER_IMAGE_VOLUMES_OR_OS_NOT_PERMITTED")


def copy_tracked(root, expected, destination):
    destination.mkdir(mode=0o755)
    for name, digest in expected.files.items():
        source = root / name
        if source.is_symlink() or not source.resolve(strict=True).is_relative_to(root):
            raise ValueError("APPLICATION_CONTAINER_SOURCE_CHANGED")
        with source.open("rb") as stream:
            raw = stream.read(16777217)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("APPLICATION_CONTAINER_SOURCE_CHANGED")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        target.write_bytes(raw)
        target.chmod(0o555 if source.stat().st_mode & 0o100 else 0o444)


def create(client, container, source, command, name, *, probe=False, contract_hash):
    if any(char in str(source) for char in (",", "\r", "\n")):
        raise ValueError("APPLICATION_CONTAINER_MOUNT_PATH_UNSUPPORTED")
    runtime = container.python_path if command.runtime == "python" else container.node_path
    arguments = command.arguments
    if probe:
        if command.runtime == "python":
            arguments = [
                "-I",
                "-c",
                "import hashlib,sys; "
                "print(hashlib.sha256(open(sys.executable,'rb').read()).hexdigest())",
            ]
        else:
            arguments = [
                "-e",
                "process.stdout.write(require('crypto').createHash('sha256').update(require('fs').readFileSync(process.execPath)).digest('hex'))",
            ]
    environment = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/tmp",
        "TMPDIR": "/tmp",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "NO_COLOR": "1",
        **command.environment,
    }
    # The daemon must stop work even if the host runner and attached CLI die.
    # GNU timeout remains PID 1; its exit also reaps descendants that created
    # another process group inside this private PID namespace.
    deadline = [
        "/usr/bin/timeout",
        "--signal=TERM",
        "--kill-after=1s",
        str(10 if probe else command.timeout_seconds) + "s",
    ]
    identifier = client.call(
        "create",
        "--pull=never",
        "--name",
        name,
        "--label",
        "org.cutback.application-check.contract=" + contract_hash,
        "--network=none",
        "--ipc=private",
        "--cgroupns=private",
        "--read-only",
        "--user=10001:10001",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--pids-limit=" + str(container.pids),
        "--memory=" + str(container.memory_mb) + "m",
        "--memory-swap=" + str(container.memory_mb) + "m",
        "--cpus=1",
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=" + str(container.tmp_mb) + "m,mode=1777",
        "--mount",
        "type=bind,source=" + str(source) + ",target=/source,readonly",
        "--workdir",
        "/source" + ("/" + command.working_directory if command.working_directory != "." else ""),
        "--no-healthcheck",
        "--log-driver=none",
        "--entrypoint=/usr/bin/env",
        container.image_id,
        "-i",
        *[key + "=" + value for key, value in environment.items()],
        *deadline,
        runtime,
        *arguments,
    ).strip()
    if not re.fullmatch(r"[a-f0-9]{64}", identifier):
        raise ValueError("APPLICATION_CONTAINER_IDENTITY_INVALID")
    value = json.loads(client.call("inspect", identifier))[0]
    host, config = value["HostConfig"], value["Config"]
    mounts = value["Mounts"]
    if (
        value["Image"] != container.image_id
        or config["User"] != "10001:10001"
        or host["NetworkMode"] != "none"
        or host["IpcMode"] != "private"
        or host["CgroupnsMode"] != "private"
        or host.get("PidMode") not in (None, "")
        or host["ReadonlyRootfs"] is not True
        or host["Privileged"]
        or host.get("CapAdd")
        or host["CapDrop"] != ["ALL"]
        or "no-new-privileges:true" not in host["SecurityOpt"]
        or host["Memory"] != container.memory_mb * 1048576
        or host["MemorySwap"] != container.memory_mb * 1048576
        or host["PidsLimit"] != container.pids
        or host["NanoCpus"] != 1000000000
        or host["LogConfig"]["Type"] != "none"
        or host["Tmpfs"]
        != {"/tmp": "rw,noexec,nosuid,nodev,size=" + str(container.tmp_mb) + "m,mode=1777"}
        or len(mounts) != 1
        or mounts[0]["Type"] != "bind"
        or mounts[0]["Destination"] != "/source"
        or mounts[0]["RW"] is not False
        or host.get("Devices")
        or host.get("PortBindings")
        or config["Entrypoint"] != ["/usr/bin/env"]
        or config.get("Labels", {}).get("org.cutback.application-check.contract") != contract_hash
        or config["Cmd"]
        != [
            "-i",
            *[key + "=" + value for key, value in environment.items()],
            *deadline,
            runtime,
            *arguments,
        ]
    ):
        raise ValueError("APPLICATION_CONTAINER_ISOLATION_NOT_ENFORCED")
    return identifier


def bounded_start(client, identifier, command):
    process = subprocess.Popen(
        client.argv("start", "--attach", identifier),
        env=client.env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    size, exceeded, lock = [0], threading.Event(), threading.Lock()

    def drain(pipe):
        try:
            while chunk := pipe.read(4096):
                with lock:
                    size[0] += len(chunk)
                    if size[0] > command.max_output_bytes:
                        exceeded.set()
        finally:
            pipe.close()

    threads = [
        threading.Thread(target=drain, args=(pipe,), daemon=True)
        for pipe in (process.stdout, process.stderr)
    ]
    started, status = time.monotonic(), "passed"
    try:
        for thread in threads:
            thread.start()
        while process.poll() is None:
            if exceeded.is_set() or time.monotonic() - started > command.timeout_seconds:
                status = "output_limit" if exceeded.is_set() else "timeout"
                try:
                    client.call("kill", identifier)
                except ValueError:
                    # A finite output producer may exit between the threshold
                    # observation and kill. Only an already stopped container
                    # resolves that race; a control outage remains an error.
                    stopped = json.loads(
                        client.call("inspect", "--format", "{{json .State}}", identifier)
                    )
                    if stopped["Running"]:
                        raise
                break
            time.sleep(0.01)
        process.wait(timeout=10)
        state = json.loads(client.call("inspect", "--format", "{{json .State}}", identifier))
        if state["Running"] or not state.get("StartedAt", "").startswith("20"):
            raise ValueError("APPLICATION_CONTAINER_EXECUTION_NOT_VERIFIED")
        if status == "passed" and (state["ExitCode"] != 0 or process.returncode != 0):
            status = "failed"
        for thread in threads:
            thread.join(timeout=2)
        if any(thread.is_alive() for thread in threads):
            status = "descendant_output_open"
        if exceeded.is_set():
            status = "output_limit"
        return {
            "command": command.key,
            "status": status,
            "exit_code": state["ExitCode"],
            "duration_seconds": round(time.monotonic() - started, 3),
            "output_bytes_observed": size[0],
            "output_retained": False,
        }
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def run_isolated_checks(contract, base, proposed):
    base, proposed = source_root(base), source_root(proposed)
    if base == proposed or base.is_relative_to(proposed) or proposed.is_relative_to(base):
        raise ValueError("APPLICATION_CHECK_INDEPENDENT_WORKSPACES_REQUIRED")
    if snapshot(base) != contract.base or snapshot(proposed) != contract.proposed:
        raise ValueError("APPLICATION_CHECK_SOURCE_CHANGED")
    client = LocalDocker()
    client.image(contract.container.image_id)
    contract_hash = fingerprint(contract.model_dump(mode="json"))
    results = []
    with tempfile.TemporaryDirectory(prefix="cutback-check-") as temporary:
        for phase, root, expected in (
            ("base", base, contract.base),
            ("proposed", proposed, contract.proposed),
        ):
            source = Path(temporary) / phase
            copy_tracked(root, expected, source)
            for command in contract.commands:
                if snapshot(root) != expected:
                    raise ValueError("APPLICATION_CHECK_SOURCE_CHANGED")
                # Runtime digest is obtained in a separate bounded invocation of
                # the same immutable image, not parsed from application output.
                name = "cutback-check-" + uuid4().hex
                try:
                    identifier = create(
                        client,
                        contract.container,
                        source,
                        command,
                        name,
                        probe=True,
                        contract_hash=contract_hash,
                    )
                    digest = client.call("start", "--attach", identifier).strip()
                    state = json.loads(
                        client.call("inspect", "--format", "{{json .State}}", identifier)
                    )
                    if state["ExitCode"] != 0 or not re.fullmatch(r"[a-f0-9]{64}", digest):
                        raise ValueError("APPLICATION_CONTAINER_RUNTIME_NOT_QUALIFIED")
                finally:
                    client.call("rm", "--force", name)
                name = "cutback-check-" + uuid4().hex
                try:
                    identifier = create(
                        client,
                        contract.container,
                        source,
                        command,
                        name,
                        contract_hash=contract_hash,
                    )
                    result = bounded_start(client, identifier, command)
                finally:
                    # Killing the CLI alone does not terminate a detached workload.
                    # Remove the owned container on success, limit, error or cancel.
                    client.call("rm", "--force", name)
                if snapshot(root) != expected:
                    result["status"] = "source_changed"
                results.append({"phase": phase, "runtime_sha256": digest, **result})
                if result["status"] == "source_changed":
                    break
            if any(result["status"] == "source_changed" for result in results):
                break
    value = {
        "schema_version": "cutback.application-check-receipt.v2",
        "contract_hash": contract_hash,
        "base_tree_hash": contract.base.tree_hash,
        "proposed_tree_hash": contract.proposed.tree_hash,
        "recorded_at": datetime.now(UTC).isoformat(),
        "results": results,
        "all_passed": len(results) == len(contract.commands) * 2
        and all(result["status"] == "passed" for result in results),
        "trust": "externally_executed_local_checks",
        "source_scope": "tracked_git_files_only",
        "isolation": "local_linux_container_network_none",
        "container": contract.container.model_dump(mode="json"),
        "customer_quality": "not_established",
    }
    receipt = {**value, "receipt_hash": fingerprint(value)}
    ContainerCheckReceipt.model_validate(receipt)
    # Keep the exact timestamp spelling used by the hash. Pydantic serializes a
    # parsed +00:00 timestamp as Z, which would otherwise change the receipt.
    return receipt
