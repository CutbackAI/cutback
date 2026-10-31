"""Refresh private runtime attribution from a verified committed source snapshot, offline."""

import json

from cutback.core.workspace import private_directory, workspace_lock, write_private


def refresh(root):
    from cutback.agent import workspace
    from cutback.application_checks import snapshot
    from cutback.instrumentation.attribution import build_source_map
    from cutback.scanner import scan

    root, directory = workspace(root)
    with workspace_lock(directory):
        before = snapshot(root)
        inventory = scan(root)
        mapping = build_source_map(root, inventory)
        if (
            mapping["source_commit"] != before.commit
            or snapshot(root) != before
            or len(mapping["files"]) > 256
            or len(json.dumps(mapping, ensure_ascii=True, indent=2).encode()) + 1 > 1048576
        ):
            raise ValueError("CURRENT_BOUNDED_SOURCE_MAP_REQUIRED")
        destination = private_directory(root, ".cutback") / "source-map.json"
        write_private(destination, mapping)
    return {
        "source_map_path": ".cutback/source-map.json",
        "source_commit": before.commit,
        "mapped_files": len(mapping["files"]),
        "provider_calls": 0,
        "application_source_changed": False,
        "runtime_restart_required": True,
    }
