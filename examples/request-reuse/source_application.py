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
    def __init__(self, *, root=None, with_cases=False):
        self.temporary = None
        if root is None:
            self.temporary = tempfile.TemporaryDirectory(prefix="cutback-owned-source-workload-")
            self.root = Path(self.temporary.name).resolve(strict=True)
        else:
            from cutback.core.workspace import prepare_private_output

            root = Path(root).absolute()
            prepare_private_output(root)
            try:
                root.mkdir(mode=0o700)
            except FileExistsError:
                raise ValueError("OWNED_SOURCE_DESTINATION_MUST_BE_NEW") from None
            prepare_private_output(root / "app.py")
            self.root = root.resolve(strict=True)
        self.module = None
        try:
            self.original = (
                Path(__file__).resolve().parents[1] / "source-workload/app.py"
            ).read_bytes()
            (self.root / "app.py").write_bytes(self.original)
            (self.root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
            if with_cases:
                cases = [
                    {
                        "id": f"{chr(65 + group)}-{repeat}",
                        "input": f"Owned billing topic {chr(65 + group)}",
                        "gold": "billing",
                        "group": f"family-{chr(65 + group)}",
                    }
                    for group in range(20)
                    for repeat in range(10)
                ]
                (self.root / "cases.jsonl").write_text(
                    "".join(json.dumps(case) + "\n" for case in cases), encoding="utf-8"
                )
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
                ("add", "app.py", ".gitignore", *(["cases.jsonl"] if with_cases else [])),
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
        if self.temporary is not None:
            self.temporary.cleanup()
