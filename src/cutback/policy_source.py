"""Read the exact clean tracked policy consumer without importing the application."""

import hashlib
from pathlib import Path, PurePosixPath

from cutback.application_checks import snapshot, source_root
from cutback.core.policy_producer import PolicySourceBinding, qualify
from cutback.core.request_profiles import safe_relative
from cutback.source_policy import MAX_POLICY_BYTES, parse_policy


def inspect_configuration(root: Path, producer_file: str):
    root = source_root(root)
    safe_relative(producer_file)
    original = snapshot(root)
    configuration_file = str(PurePosixPath(producer_file).parent / "policy.json")
    if producer_file not in original.files or configuration_file not in original.files:
        raise ValueError("POLICY_SOURCE_TRACKED_PROGRAM_AND_CONFIGURATION_REQUIRED")
    with (root / producer_file).open("rb") as stream:
        raw = stream.read(65537)
    if hashlib.sha256(raw).hexdigest() != original.files[producer_file]:
        raise ValueError("POLICY_SOURCE_CHANGED_DURING_INSPECTION")
    program = qualify(raw)
    with (root / configuration_file).open("rb") as stream:
        configuration = stream.read(MAX_POLICY_BYTES + 1)
    if hashlib.sha256(configuration).hexdigest() != original.files[configuration_file]:
        raise ValueError("POLICY_SOURCE_CHANGED_DURING_INSPECTION")
    policy = parse_policy(configuration)
    if snapshot(root) != original:
        raise ValueError("POLICY_SOURCE_CHANGED_DURING_INSPECTION")
    return policy, PolicySourceBinding(
        producer_file=producer_file,
        producer_sha256=hashlib.sha256(raw).hexdigest(),
        configuration_file=configuration_file,
        configuration_sha256=original.files[configuration_file],
        policy_sha256=policy.policy_sha256,
        source_tree_hash=original.tree_hash,
        commit=original.commit,
        program_sha256=program,
        workflow=policy.workflow,
        max_input_bytes=policy.max_input_bytes,
    )
