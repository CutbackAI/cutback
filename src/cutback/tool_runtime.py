"""Private pipe protocol for the pinned isolated record adapter image.

The broker is the only caller. Model tool arguments are nested data; the broker
constructs session/action/operation envelopes. This executable never accepts a
file path, shell command, URL, SQL program, plugin import or provider credential.
"""

import base64
import errno
import json
import os
import signal
import socket
import sys
from uuid import UUID, uuid4

from cutback.core.tool_policy import IsolatedToolPolicy, RecordSeed, canonical, content_bound
from cutback.isolated_records import IsolatedRecords

MAX_FRAME = 1048576


def isolation_status():
    """Refuse use outside the selected Linux isolation profile; expose no environment values."""
    if sys.platform != "linux" or os.getuid() != 10001:
        return False
    try:
        with open("/proc/self/status", encoding="ascii") as stream:
            status = dict(line.strip().split(":", 1) for line in stream if ":" in line)
        with open("/proc/self/mountinfo", encoding="ascii") as stream:
            root = next(line.split() for line in stream if line.split()[4] == "/")
        if (
            int(status["CapEff"], 16) != 0
            or status["NoNewPrivs"].strip() != "1"
            or status["Seccomp"].strip() != "2"
            or "ro" not in root[5].split(",")
            or set(os.listdir("/sys/class/net")) != {"lo"}
        ):
            return False
        # No inheritance of application, collector or model authority into this process.
        if any(
            any(
                marker in name.upper()
                for marker in ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "API_KEY", "PROXY")
            )
            for name in os.environ
        ):
            return False
        # Exercise the effective controls, not just daemon configuration. The
        # reserved TEST-NET destination receives no application data. A missing
        # route must deny the connect immediately; a timeout is not proof.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(1)
            if probe.connect_ex(("192.0.2.1", 9)) not in {
                errno.ENETUNREACH,
                errno.EHOSTUNREACH,
                errno.EACCES,
                errno.EPERM,
            }:
                return False
        probe_path = "/cutback-isolation-check-" + uuid4().hex
        try:
            descriptor = os.open(probe_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError as exc:
            if exc.errno not in {errno.EROFS, errno.EACCES, errno.EPERM}:
                return False
        else:
            os.close(descriptor)
            os.unlink(probe_path)
            return False
    except (OSError, KeyError, ValueError, StopIteration):
        return False
    return True


def emit(value):
    raw = canonical(value)
    if len(raw) > 300000:
        raise ValueError("ISOLATED_TOOL_RUNTIME_RESPONSE_BOUND")
    sys.stdout.buffer.write(raw + b"\n")
    sys.stdout.buffer.flush()


def read_frame():
    raw = sys.stdin.buffer.readline(MAX_FRAME + 1)
    if not raw:
        raise EOFError
    if len(raw) > MAX_FRAME or not raw.endswith(b"\n"):
        raise ValueError("ISOLATED_TOOL_RUNTIME_FRAME_BOUND")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("ISOLATED_TOOL_RUNTIME_OBJECT_REQUIRED")
    content_bound(value, MAX_FRAME)
    return value


def timeout(signum, frame):
    raise TimeoutError


def main():
    runtime = None
    try:
        alarm = getattr(signal, "alarm", None)
        alarm_signal = getattr(signal, "SIGALRM", None)
        if not isolation_status() or not callable(alarm) or alarm_signal is None:
            emit({"status": "REFUSED", "code": "ISOLATED_TOOL_KERNEL_PROFILE_REQUIRED"})
            return 2
        signal.signal(alarm_signal, timeout)
        alarm(10)
        emit({"status": "READY", "version": "cutback.isolated-tool-pipe.v1"})
        first = read_frame()
        if (
            set(first) != {"action", "policy", "seeds", "receipt_key"}
            or first["action"] != "initialize"
        ):
            raise ValueError("ISOLATED_TOOL_RUNTIME_INITIALIZATION_REQUIRED")
        policy = IsolatedToolPolicy.model_validate(first["policy"])
        if not isinstance(first["seeds"], list):
            raise ValueError("ISOLATED_TOOL_RECORDS_REQUIRED")
        seeds = [RecordSeed.model_validate(value) for value in first["seeds"]]
        key = base64.b64decode(first["receipt_key"], validate=True)
        runtime = IsolatedRecords(policy, seeds, key)
        alarm(policy.max_wall_seconds)
        emit(
            {
                "status": "INITIALIZED",
                "session_id": str(policy.session_id),
                "policy_sha256": policy.policy_sha256,
            }
        )
        for _ in range(policy.max_calls * 2 + 4):
            frame = read_frame()
            if (
                frame.get("session_id") != str(policy.session_id)
                or frame.get("project_id") != str(policy.project_id)
                or frame.get("principal_id") != str(policy.principal_id)
            ):
                emit({"status": "DENIED", "code": "ISOLATED_TOOL_RUNTIME_SCOPE_DENIED"})
                continue
            if frame.get("action") == "call" and set(frame) == {
                "action",
                "session_id",
                "project_id",
                "principal_id",
                "operation_id",
                "tool",
                "arguments",
            }:
                emit(
                    runtime.execute(UUID(frame["operation_id"]), frame["tool"], frame["arguments"])
                )
            elif frame.get("action") in {"owner_snapshot", "metadata", "close"} and set(frame) == {
                "action",
                "session_id",
                "project_id",
                "principal_id",
            }:
                if frame["action"] == "close":
                    emit({"status": "CLOSED"})
                    return 0
                if frame["action"] == "owner_snapshot":
                    emit({"status": "SNAPSHOT", "state": runtime.owner_snapshot()})
                else:
                    emit({"status": "METADATA", "metadata": runtime.metadata()})
            else:
                emit({"status": "DENIED", "code": "ISOLATED_TOOL_RUNTIME_ACTION_DENIED"})
        emit({"status": "CLOSED", "code": "ISOLATED_TOOL_RUNTIME_FRAME_LIMIT"})
        return 0
    except EOFError:
        return 0
    except Exception:
        # No traceback, exception text, seed, key, argument or result in a failure.
        try:
            emit({"status": "FAILED", "code": "ISOLATED_TOOL_RUNTIME_PROTOCOL_OR_TIME_DENIED"})
        except (OSError, ValueError):
            pass
        return 2
    finally:
        if runtime is not None:
            runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
