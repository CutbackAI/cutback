"""Reconstruct bounded exact source bytes from a refreshed private proposal.

This parser executes no application/Git code and grants no confirmation authority.
The caller must obtain the proposal through its current canonical engine first.
Both the actual forward and inverse patches must match the frozen byte hashes.
"""

import hashlib
import re

from cutback.core.request_profiles import safe_relative

MAX_SOURCE_BYTES = 1048576
MAX_PATCH_BYTES = 4 * MAX_SOURCE_BYTES
HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@\n")


def apply_exact_diff(original: bytes, patch: str, source_file: str) -> bytes:
    safe_relative(source_file)
    if (
        type(original) is not bytes
        or len(original) > MAX_SOURCE_BYTES
        or type(patch) is not str
        or len(patch.encode()) > MAX_PATCH_BYTES
        or "\n" in source_file
        or "\r" in source_file
    ):
        raise ValueError("APPLICATION_PATCH_BOUNDED_SOURCE_REQUIRED")
    source = original.decode("utf-8").splitlines(keepends=True)
    lines = patch.splitlines(keepends=True)
    if lines[:2] != ["--- a/" + source_file + "\n", "+++ b/" + source_file + "\n"]:
        raise ValueError("APPLICATION_PATCH_EXACT_FILE_HEADERS_REQUIRED")
    output, position, offset, hunks = [], 0, 2, 0
    while offset < len(lines):
        header = HUNK.fullmatch(lines[offset])
        if header is None:
            raise ValueError("APPLICATION_PATCH_HUNK_REQUIRED")
        old_start, old_count, new_start, new_count = (
            int(header[1]),
            int(header[2]) if header[2] is not None else 1,
            int(header[3]),
            int(header[4]) if header[4] is not None else 1,
        )
        old_index = old_start - (old_count != 0)
        new_index = new_start - (new_count != 0)
        if old_index < position or old_index > len(source):
            raise ValueError("APPLICATION_PATCH_SOURCE_RANGE_CHANGED")
        output.extend(source[position:old_index])
        position = old_index
        if new_index != len(output):
            raise ValueError("APPLICATION_PATCH_DESTINATION_RANGE_CHANGED")
        offset, consumed, produced = offset + 1, 0, 0
        while offset < len(lines) and not lines[offset].startswith("@@ "):
            line = lines[offset]
            if not line or line[0] not in " +-":
                raise ValueError("APPLICATION_PATCH_BODY_INVALID")
            operation, text = line[0], line[1:]
            offset += 1
            if offset < len(lines) and lines[offset] == "\\ No newline at end of file\n":
                if not text.endswith("\n"):
                    raise ValueError("APPLICATION_PATCH_NEWLINE_MARKER_INVALID")
                text = text[:-1]
                offset += 1
            if operation in " -":
                if position >= len(source) or source[position] != text:
                    raise ValueError("APPLICATION_PATCH_SOURCE_CONTEXT_CHANGED")
                position += 1
                consumed += 1
            if operation in " +":
                output.append(text)
                produced += 1
            if consumed > old_count or produced > new_count:
                raise ValueError("APPLICATION_PATCH_HUNK_COUNTS_CHANGED")
        if consumed != old_count or produced != new_count:
            raise ValueError("APPLICATION_PATCH_HUNK_COUNTS_CHANGED")
        hunks += 1
    if not hunks:
        raise ValueError("APPLICATION_PATCH_CHANGE_REQUIRED")
    output.extend(source[position:])
    result = "".join(output).encode("utf-8")
    if len(result) > MAX_SOURCE_BYTES or result == original:
        raise ValueError("APPLICATION_PATCH_BOUNDED_NONEMPTY_CHANGE_REQUIRED")
    return result


def replacement_bytes(original: bytes, artifact: dict) -> bytes:
    """Verify the complete source/forward/inverse identity, never infer authority."""
    if (
        artifact.get("status") != "DRY_RUN_ONLY"
        or artifact.get("production_deployed") is not False
        or hashlib.sha256(original).hexdigest() != artifact.get("source_sha256")
        or hashlib.sha256(artifact["diff"].encode()).hexdigest() != artifact.get("diff_sha256")
    ):
        raise ValueError("APPLICATION_PATCH_EXACT_REFRESHED_PROPOSAL_REQUIRED")
    result = apply_exact_diff(original, artifact["diff"], artifact["source_file"])
    if (
        hashlib.sha256(result).hexdigest() != artifact["verification"]["after_sha256"]
        or apply_exact_diff(result, artifact["rollback_diff"], artifact["source_file"]) != original
    ):
        raise ValueError("APPLICATION_PATCH_FORWARD_OR_ROLLBACK_BYTES_CHANGED")
    return result
