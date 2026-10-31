"""Create and run only our independent temporary fixture, never an arbitrary application."""

import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path

from cutback.agent import analyze, selected_profile
from cutback.instrumentation.attribution import build_source_map
from cutback.scanner import scan


class SourceApplication:
    def __init__(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cutback-owned-source-workload-")
        self.root = Path(self.temporary.name).resolve(strict=True)
        self.module = None
        try:
            self.original = (
                Path(__file__).resolve().parents[1] / "source-workload/app.py"
            ).read_bytes()
            (self.root / "app.py").write_bytes(self.original)
            (self.root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
            git_env = {
                key: value for key, value in os.environ.items() if not key.startswith("GIT_")
            }
            git_env.update(
                GIT_CONFIG_NOSYSTEM="1",
                GIT_CONFIG_GLOBAL=os.devnull,
                GIT_ATTR_NOSYSTEM="1",
                GIT_TERMINAL_PROMPT="0",
            )
            for arguments in (
                ("init", "--quiet"),
                ("add", "app.py", ".gitignore"),
                (
                    "-c",
                    "user.name=Owned Synthetic Example",
                    "-c",
                    "user.email=fixture@example.invalid",
                    "-c",
                    "core.hooksPath=",
                    "-c",
                    "commit.gpgSign=false",
                    "commit",
                    "--quiet",
                    "-m",
                    "Independent synthetic application",
                ),
            ):
                result = subprocess.run(
                    ["git", "-c", "core.hooksPath=", "-c", "core.fsmonitor=false", *arguments],
                    cwd=self.root,
                    capture_output=True,
                    timeout=20,
                    env=git_env,
                )
                if result.returncode:
                    raise ValueError("OWNED_FIXTURE_COMMIT_FAILED")
            state = analyze(self.root)
            self.profile = selected_profile(self.root, state)
            self.workflow = self.profile.binding.workflow_id
            self.source_map = self.root / ".cutback/source-map.json"
            self.source_map.write_text(
                json.dumps(build_source_map(self.root, scan(self.root))), encoding="utf-8"
            )
        except BaseException:
            self.close()
            raise

    def call(self, client, text):
        if self.module is None:
            spec = importlib.util.spec_from_file_location(
                "owned_workload_application", self.root / "app.py"
            )
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
            self.module.client.close()
        self.module.client = client
        return self.module.classify(text)

    def unchanged(self):
        return (self.root / "app.py").read_bytes() == self.original

    def close(self):
        self.temporary.cleanup()
