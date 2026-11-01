"""Run reviewed upstream fixture producers in a separate, credential-free environment.

The importer never runs this tool. No cloud grader, project or provider is configured.
The Azure SDK runs a local pure callable. Google exports an actual EvalResult table.
"""

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--producer-python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    executable = args.producer_python.absolute()
    destination = args.output.absolute()
    if not executable.is_file() or destination.exists() or not destination.parent.is_dir():
        parser.error("Use an existing isolated producer Python and a new output directory.")
    with tempfile.TemporaryDirectory(prefix="cutback-owned-cloud-producer-") as temporary:
        owned = Path(temporary)
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"PATH", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP", "PATHEXT"}
        }
        environment.update({"HOME": str(owned), "USERPROFILE": str(owned), "PYTHONUTF8": "1"})
        child = owned / "owned_producer.py"
        child.write_bytes(Path(__file__).with_name("produce_owned_exports.py").read_bytes())
        try:
            result = subprocess.run(
                [str(executable), "-I", str(child), str(owned)],
                cwd=owned,
                env=environment,
                capture_output=True,
                text=True,
                timeout=180,
            )
        except (OSError, subprocess.TimeoutExpired):
            parser.error("The isolated synthetic producer did not complete; diagnostics omitted.")
        if result.returncode:
            parser.error("The pinned isolated synthetic producer failed; no fixture was exported.")
        receipt = json.loads(result.stdout)
        destination.mkdir()
        for name in receipt["files"]:
            if name not in {
                "azure-ai-evaluation-1.18.10-results.json",
                "vertexai-1.153.1-metrics.jsonl",
                "vertexai-1.153.1-missing.jsonl",
            }:
                raise ValueError("UNEXPECTED_SYNTHETIC_PRODUCER_FILE")
            (destination / name).write_bytes((owned / name).read_bytes())
        (destination / "producer-receipt.json").write_text(
            json.dumps(receipt, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        print(json.dumps(receipt))


if __name__ == "__main__":
    main()
