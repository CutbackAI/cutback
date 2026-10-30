import json
import shutil

import pytest
from typer.testing import CliRunner


@pytest.mark.parametrize("entry", ["launcher", "support_sample", "retrieval_sample", "cli"])
def test_missing_git_is_actionable_before_workspace_database_or_http_setup(
    tmp_path, monkeypatch, entry
):
    from cutback.cli import app
    from cutback.demo import launch_demo, seed_support_desk
    from cutback.demo_retrieval import seed_retrieval

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    if entry == "cli":
        result = CliRunner().invoke(app, ["demo", "--no-open-browser"])
        assert result.exit_code == 2
        message = json.loads(result.stdout)
        assert message["code"] == "DEMO_GIT_REQUIRED"
        assert "Git" in message["next_action"] and "PATH" in message["next_action"]
    else:
        with pytest.raises(ValueError, match="^DEMO_GIT_REQUIRED$"):
            if entry == "launcher":
                launch_demo(open_browser=False)
            elif entry == "support_sample":
                seed_support_desk(None, "http://127.0.0.1:1", "not-used", tmp_path / "sample")
            else:
                seed_retrieval(None, None, "not-used", tmp_path / "sample")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("kind", ["tools", "security"])
def test_guarded_tool_demo_requires_a_local_image_before_creating_any_workspace(
    tmp_path, monkeypatch, kind
):
    from cutback.demo import launch_demo

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cutback.demo.require_demo_git", lambda: None)
    expected = "GUARDED_TOOL" if kind == "tools" else "GUARDED_SECURITY"
    with pytest.raises(
        ValueError, match="^" + expected + "_DEMO_REQUIRES_EXPLICIT_LOCAL_TOOL_IMAGE_ID$"
    ):
        launch_demo(open_browser=False, **{"guarded_" + kind: True})
    assert not list(tmp_path.iterdir())
