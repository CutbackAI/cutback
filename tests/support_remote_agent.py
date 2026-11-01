"""Independent installed clients preserve venv launch paths and never echo secrets."""

import json
import os
from pathlib import Path

import pytest


def remote_interpreter(configured):
    # bin/python is commonly a symlink to a managed base interpreter. Executing
    # its resolved target loses pyvenv.cfg and therefore the installed package.
    path = Path(configured).absolute()
    if not path.is_file():
        pytest.fail("REMOTE_AGENT_EXECUTABLE_UNAVAILABLE", pytrace=False)
    return path


def remote_environment():
    allowed = (
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "TMPDIR",
        "PATH",
        "PATHEXT",
        "COMSPEC",
        "HOME",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
    )
    return {
        **{key: os.environ[key] for key in allowed if key in os.environ},
        "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1",
        "NO_COLOR": "1",
    }


def remote_json(response):
    try:
        value = json.loads(response.stdout)
    except (ValueError, TypeError):
        value = None
    if not isinstance(value, dict):
        # Process status and whitelisted exception categories explain bootstrap
        # failures without exposing stdout, stderr, command arguments or tokens.
        categories = [
            name
            for name in (
                "ModuleNotFoundError",
                "ImportError",
                "SyntaxError",
                "NameError",
                "TypeError",
                "RuntimeError",
                "MemoryError",
                "Fatal Python error",
            )
            if name in (response.stderr or "")
        ]
        pytest.fail(
            "REMOTE_AGENT_JSON_PROTOCOL_FAILED "
            + json.dumps(
                {
                    "exit_code": response.returncode,
                    "stdout_bytes": len((response.stdout or "").encode()),
                    "stderr_bytes": len((response.stderr or "").encode()),
                    "stderr_categories": categories,
                }
            ),
            pytrace=False,
        )
    return value
