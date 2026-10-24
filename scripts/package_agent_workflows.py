"""Package a single maintained workflow into portable agent distributions."""

import shutil
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = root / "skills/codex/cutback/references/workflow.md"
for target in (
    root / "skills/claude-code/cutback/references/workflow.md",
    root / "skills/cursor/cutback-workflow.md",
):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
print("Portable agent workflow references packaged.")
