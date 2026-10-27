"""Run the pinned real upstream producer on owned fixtures without provider credentials."""

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--producer-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    package = args.producer_root.resolve() / "node_modules/promptfoo"
    if json.loads((package / "package.json").read_text(encoding="utf-8"))["version"] != "0.125.1":
        raise SystemExit("Use the qualified Promptfoo 0.125.1 producer")
    node = shutil.which("node")
    if not node:
        raise SystemExit("Node is required for optional fixture regeneration")
    source = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="cutback-owned-import-") as temporary:
        root = Path(temporary)
        for name in ("synthetic-provider.cjs", "promptfoo-fixture.json"):
            shutil.copyfile(source / name, root / name)
        home = root / "isolated-home"
        home.mkdir()
        env = {
            key: os.environ[key]
            for key in ("SystemRoot", "PATH", "TEMP", "TMP", "COMSPEC", "PATHEXT")
            if key in os.environ
        }
        env.update(
            {
                "HOME": str(home),
                "USERPROFILE": str(home),
                "APPDATA": str(home),
                "PROMPTFOO_CONFIG_DIR": str(home),
                "PROMPTFOO_DISABLE_TELEMETRY": "1",
                "PROMPTFOO_DISABLE_UPDATE": "1",
                "CI": "true",
                "NO_COLOR": "1",
            }
        )
        result = subprocess.run(
            [
                node,
                str(package / "dist/src/main.js"),
                "eval",
                "-c",
                "promptfoo-fixture.json",
                "--no-cache",
                "--max-concurrency",
                "1",
                "-o",
                "upstream-results.json",
            ],
            cwd=root,
            env=env,
            capture_output=True,
            timeout=90,
        )
        if result.returncode:
            raise SystemExit(
                "Synthetic upstream fixture generation failed; raw diagnostics withheld"
            )
        output = json.loads((root / "upstream-results.json").read_text(encoding="utf-8"))
        projection = {"results": output["results"]}
        encoded = json.dumps(projection, indent=2, ensure_ascii=False) + "\n"
        if re.search(r"[A-Za-z]:[\\/]|/Users/|/home/|https?://", encoded):
            raise SystemExit("Producer emitted an unexpected path or URL; publication refused")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print("Generated owned synthetic results with Promptfoo 0.125.1; no inference configured")


if __name__ == "__main__":
    main()
