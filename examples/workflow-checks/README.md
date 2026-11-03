# See a shared change affect two workflows

This independent synthetic application has triage, escalation and health workflows.
Triage and escalation import the same normalizer. No model, collector, database,
credentials or network are needed. Use the source build containing this example;
the published dev5 package does not include this new planner.

Local component qualification passed 131 checks on each pinned SDK, zero skips,
plus all four walkthroughs in a core-only installation and non-root offline
image. [Exact runtime, earlier regression and remaining gates](../../docs/evidence/application-check-planning-acceptance.json).
Reviewed public runtime `77ce700` also passed all 20 [hosted workflow jobs](../../docs/evidence/reviewed-workflow-hosted-acceptance.json).
Later documentation/CI heads still require their own protected checks in PR 83.

Run these from the Cutback source checkout with that build installed. Each command
requires a new workspace, creates two local Git commits there and retains all
artifacts. It never pushes. Preparation alone executes no application code.

```console
python examples/workflow-checks/walkthrough.py --workspace /tmp/cutback-impact-preview
python examples/workflow-checks/walkthrough.py --workspace /tmp/cutback-impact-good --execute-local
python examples/workflow-checks/walkthrough.py --workspace /tmp/cutback-impact-broken --broken --execute-local
python examples/workflow-checks/walkthrough.py --workspace /tmp/cutback-impact-resource --broken-resource --execute-local
```

On Windows use new paths such as `C:/Temp/cutback-impact-good` instead of `/tmp/...`.

Expected results:

- Preview: five selected commands, `health-full` omitted, no execution.
- Equivalent change: ten base/proposed results pass; exit zero.
- Broken change: exit one. Both triage and escalation fail on the proposed tree;
  base checks and the unchanged health guard pass.
- Broken resource: exit one with the same affected-workflow failures. Only
  `prompt.txt` changes; the shared normalizer reads and validates this resource.

Inspect `discovery.json`: both affected workflows name the changed shared file, every selected
or omitted command is listed, and workflow dependencies contain paths only.
`contract.json` works with ordinary `cutback checks run` and `checks verify`.
`receipt.json` contains statuses and hashes, not raw application output.

For an existing pair of clean application workspaces:

```console
cutback checks discover BASE_ROOT PROPOSED_ROOT --select-affected --output contract.json --report discovery.json
```

Remove `--select-affected` to keep every command, with affected ones first. Copy
neither this sample's test assertions nor its workflow declarations into a real
application without reviewing its actual checks. The declaration in
`app/pyproject.toml` demonstrates the format, including an unchanged-workflow
regression guard. Static dependency reachability does not prove semantic test
coverage. Missing declarations, opaque imports or unowned changes retain all
checks. The process runner has the permissions of its account; use an isolated
development worker. [Planner and trust limits](../../docs/application-checks.md).
