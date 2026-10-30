"""An explicitly requested browser gate must not use a different installed runner."""

import pytest
from support_browser import browser_command


def test_global_node_or_playwright_does_not_replace_missing_locked_test_dependencies(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CUTBACK_TEST_BROWSER", "true")
    monkeypatch.setattr(
        "support_browser.shutil.which", lambda name: str(tmp_path / "global-runner")
    )
    with pytest.raises(pytest.fail.Exception, match="BROWSER_GATE_DEPENDENCIES_MISSING"):
        browser_command(tmp_path / "owned-web", "owned.spec.ts")
