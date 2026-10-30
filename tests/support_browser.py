"""Run the lockfile-installed Playwright entry point, never a global/npx fallback."""

import shutil
from pathlib import Path

import pytest


def browser_command(web_directory, *selectors):
    node = shutil.which("node")
    entry = Path(web_directory) / "node_modules/@playwright/test/cli.js"
    if not node or not entry.is_file():
        pytest.fail(
            "BROWSER_GATE_DEPENDENCIES_MISSING: install the locked web dependencies "
            "with npm ci --ignore-scripts and its pinned Chromium before requesting "
            "CUTBACK_TEST_BROWSER=true; global or automatically fetched runners are refused",
            pytrace=False,
        )
    return [node, str(entry), "test", *selectors]
