"""Fail release validation on the vulnerable installer or an unintended CI interpreter."""

import os
import re
import subprocess
import sys


def main():
    result = subprocess.run(
        [os.getenv("CUTBACK_UV", "uv"), "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    match = re.match(r"uv (\d+)\.(\d+)\.(\d+)", result.stdout)
    if not match or tuple(map(int, match.groups())) < (0, 13, 4):
        raise SystemExit(
            "Upgrade uv to >=0.13.4 using a trusted OS/package-manager channel before release work."
        )
    if sys.version_info[:3] not in {(3, 11, 18), (3, 13, 18)}:
        raise SystemExit(
            "Release validation requires a reviewed Python patch version from the CI matrix."
        )
    print("Reviewed installer and interpreter verified.")


if __name__ == "__main__":
    main()
