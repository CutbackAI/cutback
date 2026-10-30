"""Owned local-container driver for the separate tool broker only.

Never instantiate this in the scanner, collector or experiment worker. They do
not receive Docker access. A dedicated broker uses this driver with its one
approved local image and project-scoped clients.
"""

import base64
import json
import os
import queue
import re
import subprocess
import tempfile
import threading
from time import monotonic
from uuid import uuid4

from cutback.core.tool_policy import IsolatedToolPolicy, ToolPolicyMetadata, canonical


def local_endpoint(value):
    if type(value) is not str or len(value) > 512 or "\0" in value or "\n" in value:
        raise ValueError("ISOLATED_TOOL_LOCAL_DOCKER_ENDPOINT_REQUIRED")
    if value.startswith("unix:///") and not any(part == ".." for part in value[7:].split("/")):
        return value
    if re.fullmatch(r"npipe:////\./pipe/[a-zA-Z0-9_.-]{1,100}", value):
        return value
    raise ValueError("ISOLATED_TOOL_LOCAL_DOCKER_ENDPOINT_REQUIRED")


def discover_local_endpoint(docker="docker"):
    result = subprocess.run([docker, "context", "inspect"], capture_output=True, timeout=10)
    if result.returncode or len(result.stdout) > 64000:
        raise ValueError("ISOLATED_TOOL_LOCAL_DOCKER_UNAVAILABLE")
    try:
        values = json.loads(result.stdout)
        if len(values) != 1:
            raise ValueError("CONTEXT")
        return local_endpoint(values[0]["Endpoints"]["docker"]["Host"])
    except (ValueError, KeyError, TypeError, IndexError):
        raise ValueError("ISOLATED_TOOL_LOCAL_DOCKER_ENDPOINT_REQUIRED") from None


def profile_matches(
    value, *, image_id, owner, session_id, broker_id=None, project_id=None, principal_id=None
):
    """Check actual daemon state before sending any seed or receipt key."""
    try:
        config, host = value["Config"], value["HostConfig"]
        labels = config["Labels"]
        return (
            value["Image"] == image_id
            and config["User"] == "10001:10001"
            and labels["org.cutback.tool.owner"] == owner
            and labels["org.cutback.tool.session"] == str(session_id)
            and (broker_id is None or labels.get("org.cutback.tool.broker") == str(broker_id))
            and (project_id is None or labels.get("org.cutback.tool.project") == str(project_id))
            and (
                principal_id is None
                or labels.get("org.cutback.tool.principal") == str(principal_id)
            )
            and host["NetworkMode"] == "none"
            and host["ReadonlyRootfs"] is True
            and host["Privileged"] is False
            and set(host["CapDrop"] or []) == {"ALL"}
            and not host.get("CapAdd")
            and not host.get("Binds")
            and not value.get("Mounts")
            and not host.get("Devices")
            and host.get("PidMode", "") == ""
            and host.get("IpcMode") == "private"
            and host.get("UTSMode", "") == ""
            and host["Memory"] == host["MemorySwap"] == 134217728
            and host["NanoCpus"] == 1000000000
            and host["PidsLimit"] == 32
            and host["LogConfig"]["Type"] == "none"
            and "no-new-privileges" in " ".join(host["SecurityOpt"] or [])
            and not any("unconfined" in item for item in host["SecurityOpt"] or [])
            and host.get("Tmpfs", {}) == {"/tmp": "rw,nosuid,nodev,noexec,size=16m"}
        )
    except (TypeError, KeyError):
        return False


def reconcile_owned_containment(endpoint, scope, *, docker="docker"):
    """Contain only an exact scoped isolated session, including after broker restart.

    No content, execution restart or user-chosen command is accepted. A daemon
    error is never confused with a successful proof of absence.
    """
    endpoint = local_endpoint(endpoint)
    from cutback.core.tool_policy import ToolContainmentScope

    scope = ToolContainmentScope.model_validate_json(scope.model_dump_json())
    with tempfile.TemporaryDirectory(prefix="cutback-tool-reconcile-") as temporary:
        env = {
            name: value
            for name, value in os.environ.items()
            if name.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "PATHEXT", "TEMP", "TMP"}
        }
        env.update(DOCKER_HOST=endpoint, DOCKER_CONFIG=temporary)

        def command(*args):
            result = subprocess.run([docker, *args], env=env, capture_output=True, timeout=10)
            if result.returncode or len(result.stdout) > 1048576:
                raise ValueError("CONTROL")
            return result.stdout

        filters = []
        for key, value in {
            "broker": scope.broker_id,
            "project": scope.project_id,
            "principal": scope.principal_id,
            "session": scope.session_id,
        }.items():
            filters.extend(["--filter", "label=org.cutback.tool." + key + "=" + str(value)])

        def remaining():
            return (
                command("container", "ls", "--all", "--no-trunc", *filters, "--format", "{{.ID}}")
                .decode("ascii")
                .split()
            )

        try:
            identifiers = remaining()
            if len(identifiers) > 1 or any(
                not re.fullmatch(r"[a-f0-9]{64}", item) for item in identifiers
            ):
                return False
            for identifier in identifiers:
                rows = json.loads(command("inspect", identifier))
                if len(rows) != 1:
                    return False
                labels = rows[0]["Config"]["Labels"]
                if labels.get(
                    "org.cutback.tool.policy"
                ) != scope.policy_sha256 or not profile_matches(
                    rows[0],
                    image_id=scope.image_id,
                    owner=labels.get("org.cutback.tool.owner"),
                    session_id=scope.session_id,
                    broker_id=scope.broker_id,
                    project_id=scope.project_id,
                    principal_id=scope.principal_id,
                ):
                    return False
                command("rm", "--force", identifier)
            return not remaining()
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            return False


class IsolatedToolSession:
    """One finite private pipe, pinned image, no mounts/network/ambient app credentials."""

    def __init__(
        self,
        policy: IsolatedToolPolicy,
        seeds,
        *,
        endpoint,
        docker="docker",
        on_prepared=None,
        broker_id=None,
    ):
        self._policy: IsolatedToolPolicy | ToolPolicyMetadata = (
            IsolatedToolPolicy.model_validate_json(policy.model_dump_json())
        )
        self._owner = str(uuid4())
        self._broker_id = broker_id or uuid4()
        self._name = "cutback-tools-" + uuid4().hex
        self._docker = docker
        self._endpoint = local_endpoint(endpoint)
        self._temporary = tempfile.TemporaryDirectory(prefix="cutback-tool-client-")
        # Empty client config avoids inherited registry credentials/proxy settings.
        self._env = {
            name: value
            for name, value in os.environ.items()
            if name.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "PATHEXT", "TEMP", "TMP"}
        }
        self._env.update(DOCKER_HOST=self._endpoint, DOCKER_CONFIG=self._temporary.name)
        self._deadline = monotonic() + policy.max_wall_seconds
        self._process: subprocess.Popen[bytes] | None = None
        self._reader_thread: threading.Thread | None = None
        self._container_id = None
        self._queue: queue.Queue = queue.Queue(maxsize=4)
        self._stopping = threading.Event()
        self._io_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._closed = False
        self._cleanup_confirmed = False
        self._watchdog = threading.Timer(policy.max_wall_seconds, self.close)
        self._watchdog.daemon = True
        try:
            if on_prepared is not None:
                on_prepared(self)
            self._start(seeds)
        except BaseException:
            self.close()
            raise

    def _command(self, *args, timeout=10):
        result = subprocess.run(
            [self._docker, *args], env=self._env, capture_output=True, timeout=timeout
        )
        if result.returncode or len(result.stdout) > 1048576:
            raise ValueError("ISOLATED_TOOL_DOCKER_CONTROL_UNAVAILABLE")
        return result.stdout

    def _start(self, seeds):
        image = json.loads(
            self._command(
                "image",
                "inspect",
                self._policy.image_id,
                timeout=max(0.01, min(10, self._deadline - monotonic())),
            )
        )
        if len(image) != 1 or image[0]["Id"] != self._policy.image_id:
            raise ValueError("ISOLATED_TOOL_APPROVED_IMAGE_UNAVAILABLE")
        if self._closed or monotonic() >= self._deadline:
            raise ValueError("ISOLATED_TOOL_STARTUP_WALL_BOUND")
        command = [
            self._docker,
            "run",
            "--rm",
            "--interactive",
            "--pull=never",
            "--name",
            self._name,
            "--label",
            "org.cutback.tool.owner=" + self._owner,
            "--label",
            "org.cutback.tool.session=" + str(self._policy.session_id),
            "--label",
            "org.cutback.tool.broker=" + str(self._broker_id),
            "--label",
            "org.cutback.tool.project=" + str(self._policy.project_id),
            "--label",
            "org.cutback.tool.principal=" + str(self._policy.principal_id),
            "--label",
            "org.cutback.tool.policy=" + self._policy.policy_sha256,
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=32",
            "--memory=128m",
            "--memory-swap=128m",
            "--cpus=1",
            "--user=10001:10001",
            "--log-driver=none",
            "--ipc=private",
            "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=16m",
            "--entrypoint=/app/.venv/bin/python",
            self._policy.image_id,
            "-I",
            "-m",
            "cutback.tool_runtime",
        ]
        with self._lifecycle_lock:
            if self._closed or monotonic() >= self._deadline:
                raise ValueError("ISOLATED_TOOL_STARTUP_WALL_BOUND")
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=self._env,
            )
            self._watchdog.interval = max(0.01, self._deadline - monotonic())
            self._watchdog.start()
        self._reader_thread = threading.Thread(target=self._reader, daemon=True)
        self._reader_thread.start()
        ready = self._read()
        if ready != {"status": "READY", "version": "cutback.isolated-tool-pipe.v1"}:
            raise ValueError("ISOLATED_TOOL_RUNTIME_DID_NOT_VALIDATE_KERNEL_PROFILE")
        container = json.loads(self._command("inspect", self._name))
        if len(container) != 1 or not profile_matches(
            container[0],
            image_id=self._policy.image_id,
            owner=self._owner,
            session_id=self._policy.session_id,
            broker_id=self._broker_id,
            project_id=self._policy.project_id,
            principal_id=self._policy.principal_id,
        ):
            raise ValueError("ISOLATED_TOOL_ACTUAL_CONTAINER_PROFILE_DENIED")
        self._container_id = container[0]["Id"]
        initialized = self._exchange(
            {
                "action": "initialize",
                "policy": self._policy.model_dump(mode="json"),
                "seeds": [seed.model_dump(mode="json") for seed in seeds],
                "receipt_key": base64.b64encode(os.urandom(32)).decode(),
            }
        )
        if initialized != {
            "status": "INITIALIZED",
            "session_id": str(self._policy.session_id),
            "policy_sha256": self._policy.policy_sha256,
        }:
            raise ValueError("ISOLATED_TOOL_RUNTIME_INITIALIZATION_DENIED")

    def _reader(self):
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            while not self._stopping.is_set():
                raw = process.stdout.readline(300001)
                if self._stopping.is_set():
                    return
                if not raw or len(raw) > 300000 or not raw.endswith(b"\n"):
                    self._queue.put(None, timeout=1)
                    return
                value = json.loads(raw)
                if not isinstance(value, dict):
                    self._queue.put(None, timeout=1)
                    return
                if not self._stopping.is_set():
                    self._queue.put(value, timeout=1)
        except (OSError, ValueError, queue.Full):
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass

    def _read(self):
        if self._closed:
            raise ConnectionError("ISOLATED_TOOL_OPERATION_UNCERTAIN_NO_AUTOMATIC_RETRY")
        remaining = self._deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("ISOLATED_TOOL_OPERATION_UNCERTAIN_NO_AUTOMATIC_RETRY")
        try:
            result = self._queue.get(timeout=remaining)
        except queue.Empty:
            raise TimeoutError("ISOLATED_TOOL_OPERATION_UNCERTAIN_NO_AUTOMATIC_RETRY") from None
        if result is None:
            raise ConnectionError("ISOLATED_TOOL_OPERATION_UNCERTAIN_NO_AUTOMATIC_RETRY")
        return result

    def _exchange(self, frame):
        raw = canonical(frame) + b"\n"
        if len(raw) > 1048576 or self._closed or monotonic() >= self._deadline:
            raise ValueError("ISOLATED_TOOL_SESSION_OR_FRAME_BOUND")
        if not self._io_lock.acquire(timeout=max(0, self._deadline - monotonic())):
            raise TimeoutError("ISOLATED_TOOL_OPERATION_UNCERTAIN_NO_AUTOMATIC_RETRY")
        try:
            process = self._process
            if self._closed or process is None or process.stdin is None:
                raise ConnectionError("ISOLATED_TOOL_OPERATION_UNCERTAIN_NO_AUTOMATIC_RETRY")
            process.stdin.write(raw)
            process.stdin.flush()
            return self._read()
        finally:
            self._io_lock.release()

    def _scope(self, action):
        return {
            "action": action,
            "session_id": str(self._policy.session_id),
            "project_id": str(self._policy.project_id),
            "principal_id": str(self._policy.principal_id),
        }

    def execute(self, operation_id, tool, arguments):
        result = self._exchange(
            {
                **self._scope("call"),
                "operation_id": str(operation_id),
                "tool": tool,
                "arguments": arguments,
            }
        )
        if (
            result.get("operation_id") != str(operation_id)
            or result.get("policy_sha256") != self._policy.policy_sha256
            or result.get("status") not in {"returned", "denied"}
        ):
            raise ValueError("ISOLATED_TOOL_OPERATION_RECEIPT_UNAVAILABLE_OR_MISMATCHED")
        return result

    def owner_snapshot(self):
        response = self._exchange(self._scope("owner_snapshot"))
        if response.get("status") != "SNAPSHOT":
            raise ValueError("ISOLATED_TOOL_OWNER_SNAPSHOT_UNAVAILABLE")
        return response["state"]

    def metadata(self):
        response = self._exchange(self._scope("metadata"))
        if response.get("status") != "METADATA":
            raise ValueError("ISOLATED_TOOL_RUNTIME_METADATA_UNAVAILABLE")
        return response["metadata"]

    def close(self, *, reconcile=False):
        with self._cleanup_lock:
            self._close_owned(reconcile=reconcile)

    def _close_owned(self, *, reconcile):
        with self._lifecycle_lock:
            if self._closed and not reconcile:
                return
            self._closed = True
        self._watchdog.cancel()
        self._stopping.set()
        try:
            # Exact previously verified daemon identity. No wildcard/container list cleanup.
            if self._process is None:
                self._cleanup_confirmed = True
            elif self._container_id:
                try:
                    self._command("rm", "--force", self._container_id)
                    self._cleanup_confirmed = True
                except (OSError, ValueError, subprocess.SubprocessError):
                    # --rm may already have removed the exact container. Confirm
                    # absence without confusing a failed daemon read with absence.
                    try:
                        remaining = self._command(
                            "container",
                            "ls",
                            "--all",
                            "--filter",
                            "id=" + self._container_id,
                            "--format",
                            "{{.ID}}",
                        )
                        self._cleanup_confirmed = not remaining.strip()
                    except (OSError, ValueError, subprocess.SubprocessError):
                        pass
            elif self._process:
                try:
                    rows = json.loads(self._command("inspect", self._name))
                    if (
                        len(rows) == 1
                        and rows[0]["Config"]["Labels"].get("org.cutback.tool.owner") == self._owner
                    ):
                        self._command("rm", "--force", rows[0]["Id"])
                        self._cleanup_confirmed = True
                except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                    pass
        finally:
            self._policy = ToolPolicyMetadata.capture(self._policy)
            try:
                if self._process:
                    try:
                        self._process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
                        self._process.wait(timeout=5)
            finally:
                self._clear_pipe_resources()

    def _clear_pipe_resources(self):
        if self._process:
            for stream in (self._process.stdin, self._process.stdout):
                if stream:
                    try:
                        stream.close()
                    except OSError:
                        pass
        if self._reader_thread:
            self._reader_thread.join(timeout=2)
            if self._reader_thread.is_alive():
                self._cleanup_confirmed = False
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        # Wake a reader already waiting when cancellation closed the pipe.
        # This sentinel retains no result or payload data.
        self._queue.put_nowait(None)
        self._temporary.cleanup()

    def containment_receipt(self):
        return {
            "session_id": str(self._policy.session_id),
            "cleanup_confirmed": self._cleanup_confirmed,
            "tool_cost_usd": None,
            "complete_task_cost_usd": None,
        }

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
