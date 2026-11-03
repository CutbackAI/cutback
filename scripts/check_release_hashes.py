"""Verify the exact qualified distributions before artifact upload or publication."""

import hashlib
import json
import os
import re
from pathlib import Path

MAX_ARTIFACT_BYTES = 128 * 1024 * 1024


def verify(directory, version, wheel_sha256, sdist_sha256):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+\.dev[0-9]+", version or ""):
        raise ValueError("RELEASE_VERSION_INVALID")
    expected = {
        f"cutback-{version}-py3-none-any.whl": wheel_sha256,
        f"cutback-{version}.tar.gz": sdist_sha256,
    }
    if any(not re.fullmatch(r"[0-9a-f]{64}", value or "") for value in expected.values()):
        raise ValueError("QUALIFIED_RELEASE_HASH_REQUIRED")
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("RELEASE_DIRECTORY_INVALID")
    entries = [path for path in directory.iterdir() if path.name != ".gitignore"]
    if {path.name for path in entries} != set(expected):
        raise ValueError("RELEASE_DISTRIBUTION_SET_MISMATCH")
    for path in entries:
        if (
            path.is_symlink()
            or not path.is_file()
            or not 0 < path.stat().st_size <= MAX_ARTIFACT_BYTES
        ):
            raise ValueError("RELEASE_DISTRIBUTION_INVALID")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != expected[path.name]:
            raise ValueError("QUALIFIED_RELEASE_BYTES_MISMATCH")
    return {"version": version, "verified_files": len(entries), "sha256": expected}


if __name__ == "__main__":
    import sys

    try:
        result = verify(
            sys.argv[1] if len(sys.argv) == 2 else "dist",
            os.environ.get("CUTBACK_RELEASE_VERSION"),
            os.environ.get("CUTBACK_EXPECTED_WHEEL_SHA256"),
            os.environ.get("CUTBACK_EXPECTED_SDIST_SHA256"),
        )
    except (OSError, ValueError, TypeError):
        raise SystemExit("RELEASE_HASH_VERIFICATION_FAILED_DETAILS_SUPPRESSED") from None
    print(json.dumps(result))
