# Agent integration

Copy `skills/codex/cutback` to your selected Codex skill directory. Copy `skills/claude-code/cutback` to the application's `.claude/skills/cutback`. For Cursor copy its `.mdc` and shared workflow into `.cursor/rules`.

Start with: “Use Cutback to scan this repository and find AI cost opportunities. Keep content capture off and do not deploy or incur API costs without approval.” Return source-linked findings, honest unknowns and concrete reviewable artifacts.

Codex/Claude frontmatter/references are validated locally; Cursor uses its documented description/alwaysApply `.mdc` format. CLI workflows run on independent synthetic fixtures. Actual discovery in every installed agent client remains a manual acceptance check, not a fabricated successful agent run.

Formats follow [Claude skills](https://code.claude.com/docs/en/skills) and [Cursor rules](https://cursor.com/docs/rules). `scripts/package_agent_workflows.py` distributes one maintained workflow. Inspect installed CLI help rather than assuming proposed commands exist.
