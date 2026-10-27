"""Exact source-map matching, initialized once; no parsing or user content on calls."""

import hashlib
import json
import re
import sys
from pathlib import Path
from types import FrameType

from cutback.core.inventory import workflow_identity


class SourceMap:
    def __init__(self, path):
        path = Path(path)
        if path.is_symlink() or path.parent.name != ".cutback" or path.stat().st_size > 1048576:
            raise ValueError("UNSAFE_SOURCE_MAP")
        self.root = path.resolve().parent.parent
        if path.parent.is_symlink():
            raise ValueError("UNSAFE_SOURCE_MAP")
        body = json.loads(path.read_text(encoding="utf-8"))
        if body.get("schema_version") != "1.0" or len(body.get("files", [])) > 256:
            raise ValueError("UNSUPPORTED_SOURCE_MAP")
        self.sites: dict[tuple[str, str], list[dict]] = {}
        self.workflows = {}
        self.source_commit = body.get("source_commit")
        total = 0
        for entry in body["files"]:
            relative = Path(entry["path"])
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not re.fullmatch(r"[a-f0-9]{64}", entry.get("sha256", ""))
            ):
                raise ValueError("UNSAFE_SOURCE_MAP_ENTRY")
            file = self.root / entry["path"]
            if file.is_symlink() or not file.resolve().is_relative_to(self.root):
                raise ValueError("UNSAFE_SOURCE_MAP_PATH")
            total += file.stat().st_size
            if file.stat().st_size > 1048576 or total > 16 * 1048576:
                raise ValueError("SOURCE_MAP_RESOURCE_BOUND")
            if (
                hashlib.sha256(file.read_text(encoding="utf-8").encode()).hexdigest()
                != entry["sha256"]
            ):
                continue  # Stale files do not inherit another revision's claimed identity.
            name = str(file.resolve())
            for site in entry.get("sites", []):
                if (
                    not isinstance(site.get("line"), int)
                    or not isinstance(site.get("end_line"), int)
                    or not 1 <= site["line"] <= site["end_line"]
                    or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,200}", site.get("call_site_id", ""))
                    or not isinstance(site.get("symbol"), str)
                ):
                    raise ValueError("INVALID_SOURCE_MAP_SITE")
                self.sites.setdefault((name, site["symbol"]), []).append(site)
            for workflow in entry.get("workflows", []):
                if not isinstance(workflow.get("symbol"), str) or not re.fullmatch(
                    r"workflow:[a-f0-9]{24}", workflow.get("workflow_id", "")
                ):
                    raise ValueError("INVALID_SOURCE_MAP_WORKFLOW")
                self.workflows[(name, workflow["symbol"])] = workflow["workflow_id"]

    def resolve(self):
        frame: FrameType | None = sys._getframe(1)
        site = None
        workflow = None
        try:
            for _ in range(48):
                if frame is None:
                    break
                name = frame.f_code.co_filename
                symbol = frame.f_code.co_qualname.replace(".<locals>.", ".")
                candidates = self.sites.get((name, symbol), ())
                matching = [
                    value
                    for value in candidates
                    if value["line"] <= frame.f_lineno <= value["end_line"]
                ]
                if len(matching) == 1 and site is None:
                    site = matching[0]["call_site_id"]
                if site and workflow is None:
                    workflow = self.workflows.get((name, symbol))
                frame = frame.f_back
            if site:
                return {
                    "call_site_id": site,
                    "workflow_id": workflow,
                    "source_commit": self.source_commit,
                }
            return {}
        finally:
            del frame


def build_source_map(root, inventory, *, replacements=None):
    root = Path(root).resolve()
    replacements = replacements or {}
    files = {}

    def entry(relative):
        if relative not in files:
            data = replacements.get(relative)
            if data is None:
                data = (root / relative).read_text(encoding="utf-8")
            files[relative] = {
                "path": relative,
                "sha256": hashlib.sha256(data.encode()).hexdigest(),
                "sites": [],
                "workflows": [],
            }
        return files[relative]

    for site in inventory["call_sites"]:
        if site.get("language", "python") != "python":
            continue
        entry(site["source_file"])["sites"].append(
            {
                "call_site_id": site["call_site_id"],
                "symbol": site["source_symbol"],
                "line": site["line"],
                "end_line": site.get("end_line") or site["line"],
            }
        )
        for workflow in site.get("entry_points", []):
            relative = workflow["file"]
            identity, symbol = workflow_identity(relative, workflow["symbol"])
            value = {"symbol": symbol, "workflow_id": identity}
            if value not in entry(relative)["workflows"]:
                entry(relative)["workflows"].append(value)
    return {
        "schema_version": "1.0",
        "source_commit": inventory["project"]["commit"],
        "files": list(files.values()),
    }
