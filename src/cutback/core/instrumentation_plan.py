"""Shared parse-only instrumentation proposal, used by CLI and durable agent jobs."""

import difflib
from pathlib import Path


def reject(code, next_action):
    raise ValueError(code)


def refresh_source_map(target: Path):
    """Rebind metadata to a clean revision without editing or importing application code."""
    from cutback.application_checks import snapshot, source_root
    from cutback.core.workspace import private_directory, workspace_lock, write_private
    from cutback.instrumentation.attribution import SourceMap, build_source_map
    from cutback.scanner import scan

    root = source_root(target)
    directory = private_directory(root, ".cutback")
    with workspace_lock(directory):
        before = snapshot(root)
        value = build_source_map(root, scan(root))
        if snapshot(root) != before or value["source_commit"] != before.commit:
            raise ValueError("SOURCE_CHANGED_DURING_MAP_REFRESH")
        # Keep map limits identical to the runtime reader, without replacing an
        # existing useful map with a value the runtime would refuse to load.
        import json

        if (
            len(value["files"]) > 256
            or len(json.dumps(value, sort_keys=True, ensure_ascii=True, indent=2).encode()) + 1
            > 1048576
            or sum((root / entry["path"]).stat().st_size for entry in value["files"]) > 16 * 1048576
        ):
            raise ValueError("SOURCE_MAP_RESOURCE_BOUND")
        write_private(directory / "source-map.json", value)
        SourceMap(directory / "source-map.json")
    return {
        "status": "SOURCE_MAP_REFRESHED",
        "source_commit": before.commit,
        "mapped_files": len(value["files"]),
        "source_map_path": ".cutback/source-map.json",
        "provider_calls": 0,
        "application_source_changed": False,
        "source_lineage": "client_reported_not_independent_attestation",
    }


def instrument_plan(target: Path, entrypoint: Path | None = None, handler: str | None = None):
    import ast

    root = target.resolve(strict=True)
    if entrypoint is None:
        candidates = [
            root / p
            for p in ("main.py", "app.py", "src/main.py", "__main__.py")
            if (root / p).is_file()
        ]
        if len(candidates) != 1:
            reject(
                "ENTRYPOINT_REQUIRED",
                "specify --entrypoint relative/path.py after inspecting process startup",
            )
        path = candidates[0]
    else:
        path = root / entrypoint
    if (
        path.is_symlink()
        or not path.resolve().is_relative_to(root)
        or path.stat().st_size > 1_048_576
    ):
        reject("UNSAFE_ENTRYPOINT", "choose a small Python source file inside target")
    before = path.read_bytes().decode("utf-8")
    tree = ast.parse(before)
    if handler and not any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == handler
        for node in tree.body
    ):
        reject("HANDLER_NOT_FOUND", "choose the inspected top-level serverless handler")
    map_path = root / ".cutback" / "source-map.json"
    if map_path.is_symlink() or map_path.parent.is_symlink():
        reject("UNSAFE_SOURCE_MAP", "use an ordinary .cutback directory inside the application")
    if "# cutback:bootstrap" in before:
        reject(
            "ALREADY_INSTRUMENTED", "inspect existing bootstrap and avoid duplicate instrumentation"
        )
    from cutback.core.policy_producer import qualify as qualify_policy

    try:
        qualify_policy(before.encode("utf-8"))
    except ValueError:
        pass
    else:
        reject(
            "SOURCE_POLICY_EXTERNAL_STARTUP_REQUIRED",
            "initialize observation in the actual process entry point outside the fixed consumer",
        )
    insert_after = 0
    for i, node in enumerate(tree.body):
        if (
            i == 0
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ) or (isinstance(node, ast.ImportFrom) and node.module == "__future__"):
            insert_after = node.end_lineno or node.lineno
        else:
            break
    bootstrap = (
        "\n# cutback:bootstrap - observation only; requires CUTBACK_ENABLED=true\n"
        "import os as _cutback_os\n"
        'if _cutback_os.getenv("CUTBACK_ENABLED", "false").lower() == "true":\n'
        "    try:\n"
        "        import cutback as _cutback\n"
        "        from pathlib import Path as _cutback_Path\n"
        "        _cutback_observer = _cutback.init(\n"
        '            service_name=_cutback_os.environ["CUTBACK_SERVICE_NAME"],\n'
        '            environment=_cutback_os.environ["CUTBACK_ENVIRONMENT"],\n'
        "            capture_content=False,\n"
        "            source_map=str(_cutback_Path(__file__).resolve()\n"
        f"                .parents[{len(path.relative_to(root).parts) - 1}] "
        '/ ".cutback" / "source-map.json"),\n'
        "        )\n"
        "        import atexit as _cutback_atexit\n"
        "        _cutback_atexit.register(_cutback_observer.close, 0.3)\n"
        "    except Exception:\n"
        "        import warnings as _cutback_warnings\n"
        '        _cutback_warnings.warn("Cutback initialization failed; inference continues",\n'
        "                               RuntimeWarning)\n\n"
    )
    lines = before.splitlines(keepends=True)
    after = "".join(lines[:insert_after]) + bootstrap + "".join(lines[insert_after:])
    if handler:
        after += (
            '\nif "_cutback_observer" in globals():\n'
            "    from cutback.instrumentation.serverless import (\n"
            "        instrument_handler as _cutback_handler)\n"
            f"    {handler} = _cutback_handler(\n"
            f"        _cutback_observer, max_flush_seconds=0.1)({handler})\n"
        )
    ast.parse(after)
    relative = path.relative_to(root).as_posix()
    from cutback.instrumentation.attribution import build_source_map
    from cutback.scanner import scan

    inventory = scan(root)
    for site in inventory["call_sites"]:
        if site["source_file"] == relative and site["line"] > insert_after:
            site["line"] += len(bootstrap.splitlines())
            site["end_line"] += len(bootstrap.splitlines())
    source_map = build_source_map(root, inventory, replacements={relative: after})
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
        )
    )
    return {
        "diff": diff,
        "source_map": source_map,
        "source_file": relative,
        "after": after,
        "before_sha256": __import__("hashlib").sha256(before.encode()).hexdigest(),
        "lifecycle": "bounded_handler_flush_and_process_close"
        if handler
        else "bounded_process_close; serverless needs --handler",
    }
