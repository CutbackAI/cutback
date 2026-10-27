# Agent integration

Copy `skills/codex/cutback` to your selected Codex skill directory. Copy `skills/claude-code/cutback` to the application's `.claude/skills/cutback`. For Cursor copy its `.mdc` and shared workflow into `.cursor/rules`.

Start with: “Use Cutback to scan this repository and find AI cost opportunities. Keep content capture off and do not deploy or incur API costs without approval.” Return source-linked findings, honest unknowns and concrete reviewable artifacts.

For a complete copyable request, isolated tool installation and the acceptance checkpoint inside an unrelated application, use [application onboarding](application-onboarding.md). Keep application source, reports, names and deployment details in that application's private workspace. Public Cutback examples and releases use only generic synthetic data.

Codex/Claude frontmatter/references are validated locally; Cursor uses its documented description/alwaysApply `.mdc` format. CLI workflows run on independent synthetic fixtures. Actual discovery in every installed agent client remains a manual acceptance check, not a fabricated successful agent run.

Formats follow [Claude skills](https://code.claude.com/docs/en/skills) and [Cursor rules](https://cursor.com/docs/rules). `scripts/package_agent_workflows.py` distributes one maintained workflow. Inspect installed CLI help rather than assuming proposed commands exist.

Start the resumable [agent workflow](agent-workflow.md) inside the application. It automates permitted preparation and source-bound planning; the actual coding agent handles inspected application changes and tests.

## Qualify an installed agent client

Use a disposable copy of `cutback sample` with the application's `.cutback/` ignored. Ask the actual client to discover the installed workflow, run doctor/offline optimize/status, and produce an instrumentation plan. Confirm that it did not execute application imports, read environment secrets, change pins, make provider calls, upload source or deploy. Record the actual client/version, installed rule path, tool permissions, output files and unresolved sites. Then review a separate synthetic collector run. Parser/frontmatter tests and a CLI consumer test do not substitute for this client exercise.

Cutback preserves an existing OpenTelemetry trace/span context and adds a child span only within an explicitly named workflow. It does not install an OTel provider or replace your tracing backend. Overlapping SDK wrappers are refused to prevent duplicate observations; choose one SDK observer. Cutback can consume an existing OTel context without installing a tracer provider, and shutdown restores its own hooks. The supported SDK matrix is exact; arbitrary instrumentation-stack combinations are not claimed. An existing tracer can instead use the bounded [OTLP JSON metadata profile](otel-ingestion.md), after qualifying its filtered producer. Preserve your existing tracing package pins and qualify coexistence with its synthetic transport before enabling a private application.
