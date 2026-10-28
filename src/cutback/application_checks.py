"""Explicit local checks of two frozen Git trees. Never imported by scanner/server.

This is a bounded process runner, not an OS sandbox. Run only reviewed commands in
independent workspaces. Receipts attest local execution, not customer task quality.
"""

import hashlib
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast
from xml.etree.ElementTree import Element, SubElement, tostring

from pydantic import Field, StrictBool, model_validator

from cutback.core.models import Identifier, StrictModel
from cutback.core.prompts import fingerprint
from cutback.core.request_profiles import safe_relative


class ApplicationCommand(StrictModel):
    key: Identifier
    runtime: Literal["python", "node"] = "python"
    arguments: list[str] = Field(min_length=1, max_length=32)
    working_directory: str = "."
    environment: dict[str, str] = Field(default_factory=dict, max_length=20)
    timeout_seconds: int = Field(default=60, ge=1, le=300)
    max_output_bytes: int = Field(default=65536, ge=1, le=1048576)

    @model_validator(mode="after")
    def bounded(self):
        if self.working_directory != ".":
            safe_relative(self.working_directory)
        if any(len(arg.encode()) > 2048 or "\0" in arg for arg in self.arguments):
            raise ValueError("APPLICATION_CHECK_ARGUMENT_BOUND_EXCEEDED")
        for key, value in self.environment.items():
            # Explicit fixture variables only. No inherited HOME, PATH, cloud/provider
            # keys, collector credentials, proxies or runtime/module injection settings.
            if (
                not re.fullmatch(r"CUTBACK_FIXTURE_[A-Z0-9_]{1,48}", key)
                or any(word in key for word in ("SECRET", "TOKEN", "KEY", "PASSWORD", "CREDENTIAL"))
                or len(value.encode()) > 2048
                or "\0" in value
            ):
                raise ValueError("APPLICATION_CHECK_ENVIRONMENT_NOT_PERMITTED")
        return self


class SourceTree(StrictModel):
    commit: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    files: dict[str, str] = Field(min_length=1, max_length=10000)
    tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def integrity(self):
        for name, value in self.files.items():
            safe_relative(name)
            if not re.fullmatch(r"[a-f0-9]{64}", value):
                raise ValueError("APPLICATION_SOURCE_DIGEST_INVALID")
        if fingerprint(self.files) != self.tree_hash:
            raise ValueError("APPLICATION_SOURCE_TREE_HASH_INVALID")
        return self


class ApplicationCheckContract(StrictModel):
    schema_version: Literal["cutback.application-check.v1"] = "cutback.application-check.v1"
    base: SourceTree
    proposed: SourceTree
    commands: list[ApplicationCommand] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique(self):
        if len({c.key for c in self.commands}) != len(self.commands):
            raise ValueError("APPLICATION_CHECK_COMMAND_KEYS_MUST_BE_UNIQUE")
        return self


class ApplicationCheckResult(StrictModel):
    phase: Literal["base", "proposed"]
    command: Identifier
    status: Literal[
        "passed", "failed", "timeout", "output_limit", "source_changed", "descendant_output_open"
    ]
    exit_code: int
    duration_seconds: float = Field(ge=0, le=3600)
    output_bytes_observed: int = Field(ge=0)
    output_retained: Literal[False]
    runtime_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def consistent(self):
        if self.status == "passed" and self.exit_code != 0:
            raise ValueError("APPLICATION_CHECK_PASS_REQUIRES_ZERO_EXIT")
        return self


class ApplicationCheckReceipt(StrictModel):
    schema_version: Literal["cutback.application-check-receipt.v1"]
    contract_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    base_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    proposed_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    recorded_at: datetime
    results: list[ApplicationCheckResult] = Field(min_length=1, max_length=40)
    all_passed: StrictBool
    trust: Literal["externally_executed_local_checks"]
    source_scope: Literal["tracked_git_files_only"]
    isolation: Literal["minimal_environment_no_os_sandbox"]
    customer_quality: Literal["not_established"]
    receipt_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


def git(root, *arguments):
    env = {k: os.environ[k] for k in ("SYSTEMROOT", "WINDIR") if k in os.environ}
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_ALLOW_PROTOCOL": "",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    executable = shutil.which("git")
    if not executable:
        raise ValueError("APPLICATION_CHECK_GIT_REQUIRED")
    job = WindowsJob() if os.name == "nt" else None
    process = None
    try:
        process = subprocess.Popen(
            [executable, "-c", "core.fsmonitor=false", "-C", str(root), *arguments],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NO_WINDOW | 0x4 if os.name == "nt" else 0,
        )
        if job:
            job.assign_and_resume(process)
        assert process.stdout is not None
        pipe = process.stdout
        chunks, exceeded = [], threading.Event()

        def collect():
            size = 0
            try:
                while chunk := pipe.read(8192):
                    size += len(chunk)
                    if size > 2097152:
                        exceeded.set()
                        break
                    chunks.append(chunk)
            finally:
                pipe.close()

        reader = threading.Thread(target=collect, daemon=True)
        reader.start()
        started = time.monotonic()
        while process.poll() is None and not exceeded.is_set() and time.monotonic() - started < 15:
            time.sleep(0.01)
        timed_out = process.poll() is None and not exceeded.is_set()
        stop_process(process, job)
        reader.join(timeout=2)
        if process.returncode or exceeded.is_set() or timed_out or reader.is_alive():
            raise ValueError("APPLICATION_CHECK_GIT_OR_SOURCE_BOUND_FAILED")
        return b"".join(chunks)
    except (OSError, subprocess.SubprocessError):
        raise ValueError("APPLICATION_CHECK_GIT_UNAVAILABLE") from None
    finally:
        if process is not None:
            stop_process(process, job)
        elif job:
            job.close()


def snapshot(root: Path):
    root = root.absolute()
    if root.is_symlink() or any(p.is_symlink() for p in root.parents):
        raise ValueError("APPLICATION_CHECK_SYMLINK_ROOT_DENIED")
    root = root.resolve(strict=True)
    if Path(os.fsdecode(git(root, "rev-parse", "--show-toplevel")).strip()).resolve() != root:
        raise ValueError("APPLICATION_CHECK_REPOSITORY_ROOT_REQUIRED")
    # Do not run git status/diff: clean filters and fsmonitor can execute repository
    # commands even during inspection. These plumbing reads never apply filters.
    committed, indexed = {}, {}
    for raw in git(root, "ls-tree", "--full-tree", "-r", "-z", "HEAD").split(b"\0"):
        if not raw:
            continue
        header, name = raw.split(b"\t", 1)
        mode, kind, object_id = header.decode("ascii").split()
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError("APPLICATION_CHECK_UNSAFE_TRACKED_FILE")
        committed[name.decode("utf-8")] = (mode, object_id)
    for raw in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not raw:
            continue
        header, name = raw.split(b"\t", 1)
        mode, object_id, stage = header.decode("ascii").split()
        if stage != "0":
            raise ValueError("APPLICATION_CHECK_CLEAN_TRACKED_WORKSPACE_REQUIRED")
        indexed[name.decode("utf-8")] = (mode, object_id)
    if committed != indexed or git(root, "ls-files", "--others", "--exclude-standard", "-z"):
        raise ValueError("APPLICATION_CHECK_CLEAN_TRACKED_WORKSPACE_REQUIRED")
    files: dict[str, str] = {}
    total = 0
    for name, (mode, object_id) in committed.items():
        safe_relative(name)
        file = root / name
        if (
            file.is_symlink()
            or any(p.is_symlink() for p in file.parents if p.is_relative_to(root))
            or not file.is_file()
            or not file.resolve().is_relative_to(root)
        ):
            raise ValueError("APPLICATION_CHECK_UNSAFE_TRACKED_FILE")
        size = file.stat().st_size
        if os.name != "nt" and bool(file.stat().st_mode & 0o100) != (mode == "100755"):
            raise ValueError("APPLICATION_CHECK_EXECUTABLE_MODE_CHANGED")
        total += size
        if size > 16777216 or total > 67108864 or len(files) >= 10000:
            raise ValueError("APPLICATION_CHECK_SOURCE_BYTES_EXCEEDED")
        raw = file.read_bytes()

        def blob_id(value, object_id=object_id):
            hasher = hashlib.new("sha1" if len(object_id) == 40 else "sha256")
            hasher.update(b"blob " + str(len(value)).encode() + b"\0" + value)
            return hasher.hexdigest()

        if blob_id(raw) != object_id:
            # A portable checkout may use CRLF. Only UTF-8 text can take this
            # explicit normalization path; the receipt still binds exact raw bytes.
            try:
                raw.decode("utf-8")
            except UnicodeError:
                raise ValueError("APPLICATION_CHECK_CLEAN_TRACKED_WORKSPACE_REQUIRED") from None
            if b"\0" in raw or blob_id(raw.replace(b"\r\n", b"\n")) != object_id:
                raise ValueError("APPLICATION_CHECK_CLEAN_TRACKED_WORKSPACE_REQUIRED")
        files[name] = hashlib.sha256(raw).hexdigest()
    return SourceTree(
        commit=git(root, "rev-parse", "HEAD").decode().strip(),
        files=files,
        tree_hash=fingerprint(files),
    )


def prepare(base: Path, proposed: Path, commands):
    a, b = base.resolve(strict=True), proposed.resolve(strict=True)
    if a == b or a.is_relative_to(b) or b.is_relative_to(a):
        raise ValueError("APPLICATION_CHECK_INDEPENDENT_WORKSPACES_REQUIRED")
    return ApplicationCheckContract(
        base=snapshot(base), proposed=snapshot(proposed), commands=commands
    )


def stop_process(process, windows_job=None):
    if getattr(process, "_cutback_stopped", False):
        return
    if os.name == "nt":
        if windows_job is not None:
            windows_job.close()
        if process.poll() is None:
            process.kill()
    else:
        try:
            cast(Any, os).killpg(process.pid, cast(Any, signal).SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=10)
    process._cutback_stopped = True


class WindowsJob:
    """Owned job, closed on every exit. A suspended child cannot escape assignment."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_int64),
                ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD),
                ("minimum", ctypes.c_size_t),
                ("maximum", ctypes.c_size_t),
                ("active", wintypes.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class IO(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_uint64)
                for name in (
                    "read_ops",
                    "write_ops",
                    "other_ops",
                    "read_bytes",
                    "write_bytes",
                    "other_bytes",
                )
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("basic", Basic),
                ("io", IO),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t),
                ("peak_job_memory", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.handle or not kernel.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            self.close()
            raise ValueError("APPLICATION_CHECK_PROCESS_CONTAINMENT_UNAVAILABLE")

    def assign_and_resume(self, process):
        import ctypes
        from ctypes import wintypes

        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ValueError("APPLICATION_CHECK_PROCESS_CONTAINMENT_UNAVAILABLE")
        resume = ctypes.WinDLL("ntdll").NtResumeProcess
        resume.argtypes = [wintypes.HANDLE]
        resume.restype = ctypes.c_long
        if resume(int(process._handle)) != 0:
            raise ValueError("APPLICATION_CHECK_PROCESS_RESUME_FAILED")

    def close(self):
        if self.handle:
            self.kernel.TerminateJobObject(self.handle, 1)
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def execute_command(root, command):
    runtime = Path(sys.executable if command.runtime == "python" else shutil.which("node") or "")
    if not runtime.is_file():
        raise ValueError("APPLICATION_CHECK_RUNTIME_UNAVAILABLE")
    cwd = root / command.working_directory
    if not cwd.resolve(strict=True).is_relative_to(root) or cwd.is_symlink():
        raise ValueError("APPLICATION_CHECK_WORKING_DIRECTORY_UNSAFE")
    env = {k: os.environ[k] for k in ("SYSTEMROOT", "WINDIR") if k in os.environ}
    env.update(command.environment)
    env.update(
        {
            "PATH": str(runtime.parent),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "NO_COLOR": "1",
        }
    )
    started = time.monotonic()
    job = WindowsJob() if os.name == "nt" else None
    try:
        process = subprocess.Popen(
            [str(runtime), *command.arguments],
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW | 0x4
            if os.name == "nt"
            else 0,
        )
    except BaseException:
        if job:
            job.close()
        raise
    if job:
        try:
            job.assign_and_resume(process)
        except BaseException:
            stop_process(process, job)
            raise
    size, lock, exceeded = [0], threading.Lock(), threading.Event()

    def drain(pipe):
        try:
            while chunk := pipe.read(4096):
                with lock:
                    size[0] += len(chunk)
                    if size[0] > command.max_output_bytes:
                        exceeded.set()
        finally:
            pipe.close()

    readers = [
        threading.Thread(target=drain, args=(pipe,), daemon=True)
        for pipe in (process.stdout, process.stderr)
    ]
    status = "passed"
    try:
        for thread in readers:
            thread.start()
        while process.poll() is None:
            if exceeded.is_set() or time.monotonic() - started > command.timeout_seconds:
                status = "output_limit" if exceeded.is_set() else "timeout"
                break
            time.sleep(0.01)
    finally:
        stop_process(process, job)  # Also reap descendants after exit or cancellation.
    for thread in readers:
        thread.join(timeout=2)
    if any(thread.is_alive() for thread in readers):
        status = "descendant_output_open"
    if exceeded.is_set():
        status = "output_limit"
    if status == "passed" and process.returncode:
        status = "failed"
    return {
        "command": command.key,
        "status": status,
        "exit_code": process.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "output_bytes_observed": size[0],
        "output_retained": False,
        "runtime_sha256": hashlib.sha256(runtime.read_bytes()).hexdigest(),
    }


def run_checks(
    contract: ApplicationCheckContract, base: Path, proposed: Path, *, execute_local=False
):
    if not execute_local:
        raise ValueError("APPLICATION_CHECK_EXPLICIT_LOCAL_EXECUTION_REQUIRED")
    if (
        base.resolve() == proposed.resolve()
        or base.resolve().is_relative_to(proposed.resolve())
        or proposed.resolve().is_relative_to(base.resolve())
    ):
        raise ValueError("APPLICATION_CHECK_INDEPENDENT_WORKSPACES_REQUIRED")
    if snapshot(base) != contract.base or snapshot(proposed) != contract.proposed:
        raise ValueError("APPLICATION_CHECK_SOURCE_CHANGED")
    results = []
    for phase, root, expected in (
        ("base", base.resolve(), contract.base),
        ("proposed", proposed.resolve(), contract.proposed),
    ):
        for command in contract.commands:
            if snapshot(root) != expected:
                raise ValueError("APPLICATION_CHECK_SOURCE_CHANGED")
            result = execute_command(root, command)
            try:
                changed = snapshot(root) != expected
            except ValueError:
                changed = True
            if changed:
                result["status"] = "source_changed"
            results.append({"phase": phase, **result})
            if result["status"] == "source_changed":
                break
        if any(r["status"] == "source_changed" for r in results):
            break
    value = {
        "schema_version": "cutback.application-check-receipt.v1",
        "contract_hash": fingerprint(contract.model_dump(mode="json")),
        "base_tree_hash": contract.base.tree_hash,
        "proposed_tree_hash": contract.proposed.tree_hash,
        "recorded_at": datetime.now(UTC).isoformat(),
        "results": results,
        "all_passed": len(results) == len(contract.commands) * 2
        and all(r["status"] == "passed" for r in results),
        "trust": "externally_executed_local_checks",
        "source_scope": "tracked_git_files_only",
        "isolation": "minimal_environment_no_os_sandbox",
        "customer_quality": "not_established",
    }
    return {**value, "receipt_hash": fingerprint(value)}


def verify(contract, receipt, base, proposed):
    ApplicationCheckReceipt.model_validate(receipt)
    value = {k: v for k, v in receipt.items() if k != "receipt_hash"}
    if (
        fingerprint(value) != receipt.get("receipt_hash")
        or receipt.get("contract_hash") != fingerprint(contract.model_dump(mode="json"))
        or receipt.get("base_tree_hash") != contract.base.tree_hash
        or receipt.get("proposed_tree_hash") != contract.proposed.tree_hash
        or snapshot(base) != contract.base
        or snapshot(proposed) != contract.proposed
    ):
        raise ValueError("APPLICATION_CHECK_RECEIPT_OR_SOURCE_MISMATCH")
    expected = {
        (phase, command.key) for phase in ("base", "proposed") for command in contract.commands
    }
    results = receipt.get("results", [])
    if (
        len(results) != len(expected)
        or {(r.get("phase"), r.get("command")) for r in results} != expected
        or not all(r.get("status") == "passed" for r in results)
        or receipt.get("all_passed") is not True
    ):
        raise ValueError("APPLICATION_CHECKS_NOT_ALL_PASSED")
    return {
        "status": "PASSED_RECORDED_LOCAL_CHECKS",
        "contract_hash": receipt["contract_hash"],
        "authenticity": "not_independently_attested",
    }


def junit(receipt):
    suite = Element(
        "testsuite",
        {
            "name": "Cutback application checks",
            "tests": str(len(receipt["results"])),
            "failures": str(sum(r["status"] != "passed" for r in receipt["results"])),
        },
    )
    for result in receipt["results"]:
        case = SubElement(
            suite,
            "testcase",
            {
                "classname": result["phase"],
                "name": result["command"],
                "time": str(result["duration_seconds"]),
            },
        )
        if result["status"] != "passed":
            SubElement(
                case, "failure", {"message": result["status"]}
            ).text = "Local application check failed. Output is not retained."
    return tostring(suite, encoding="unicode", xml_declaration=True)
