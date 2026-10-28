"""Create two independent owned synthetic Git workspaces; never edits an application."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

APPLICATION = """import os

def classify(text):
    return "urgent" if "urgent" in text.lower() else "routine"

assert "CUTBACK_CONTROL_TOKEN" not in os.environ
assert "OPENAI_API_KEY" not in os.environ
assert classify("URGENT synthetic ticket") == "urgent"
assert classify("owned normal ticket") == "routine"
"""


def create(destination):
    destination = Path(destination).absolute()
    if destination.exists():
        raise ValueError("SYNTHETIC_CHECK_DESTINATION_MUST_NOT_EXIST")
    destination.mkdir(parents=True)
    for name in ("base", "proposed"):
        root = destination / name
        root.mkdir()
        (root / "app.py").write_text(APPLICATION, encoding="utf-8")
        (root / "check_application.py").write_text(
            'if __name__ == "__main__":\n    import app\n', encoding="utf-8"
        )
        (root / "release.txt").write_text(name + "\n", encoding="utf-8")
        for arguments in (
            ("init",),
            ("add", "app.py", "check_application.py", "release.txt"),
            (
                "-c",
                "user.name=Synthetic",
                "-c",
                "user.email=synthetic@example.invalid",
                "commit",
                "-m",
                "Owned synthetic application",
            ),
        ):
            env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ}
            env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
            result = subprocess.run(
                [shutil.which("git"), "-C", str(root), *arguments],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=10,
                env=env,
            )
            if result.returncode:
                raise ValueError("SYNTHETIC_CHECK_GIT_SETUP_FAILED")
    return destination


if __name__ == "__main__":
    print(create(sys.argv[1]))
