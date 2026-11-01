"""Actual unified patches, exact bytes and malformed/stale proposal refusal."""

import difflib
import hashlib

import pytest

from cutback.application_patch import apply_exact_diff, replacement_bytes


def patch(before, after, path="application.py"):
    return "".join(
        line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
        for line in difflib.unified_diff(
            before.decode().splitlines(keepends=True),
            after.decode().splitlines(keepends=True),
            fromfile="a/" + path,
            tofile="b/" + path,
        )
    )


@pytest.mark.parametrize(
    "before,after",
    [
        (b'a = "baseline"\n', b'a = "cheap"\n'),
        (b'a = "baseline"', b'a = "cheap"'),
        (b'a = "baseline"\r\n', b'a = "cheap"\r\n'),
        (b"", b'a = "cheap"\n'),
        (b'a = "baseline"\n', b""),
        (b"line\n" * 20, b"first\n" + b"line\n" * 18 + b"last\n"),
    ],
)
def test_exact_forward_inverse_utf8_and_newline_bytes(before, after):
    forward, inverse = patch(before, after), patch(after, before)
    assert apply_exact_diff(before, forward, "application.py") == after
    artifact = {
        "status": "DRY_RUN_ONLY",
        "production_deployed": False,
        "source_file": "application.py",
        "source_sha256": hashlib.sha256(before).hexdigest(),
        "diff_sha256": hashlib.sha256(forward.encode()).hexdigest(),
        "diff": forward,
        "rollback_diff": inverse,
        "verification": {"after_sha256": hashlib.sha256(after).hexdigest()},
    }
    assert replacement_bytes(before, artifact) == after
    artifact["rollback_diff"] = forward
    with pytest.raises(ValueError):
        replacement_bytes(before, artifact)


@pytest.mark.parametrize(
    "change",
    [
        lambda value: value.replace("--- a/application.py", "--- a/other.py"),
        lambda value: value.replace("@@ -1 +1 @@", "@@ -2 +1 @@"),
        lambda value: value.replace("@@ -1 +1 @@", "@@ -1,2 +1 @@"),
        lambda value: value.replace("-baseline", "-stale"),
        lambda value: value + "+extra\n",
    ],
)
def test_wrong_path_ranges_counts_context_and_extra_body_are_refused(change):
    before, after = b"baseline\n", b"cheap\n"
    with pytest.raises(ValueError):
        apply_exact_diff(before, change(patch(before, after)), "application.py")
