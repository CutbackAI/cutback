# Finish a confirmed campaign without copying repositories

Working-source feature under qualification. Published PyPI `0.1.0.dev5` does not
contain `campaign finish`. Use a matching source collector/agent and inspect the
exact acceptance state in [progress](progress.md).

Complete the approved [campaign](improvement-campaigns.md) through its untouched
confirmation. Then run this command from the original application workspace:

```sh
cutback campaign finish --workspace ../private-campaign-review \
  --purpose-reference application-change-review
```

The preview refreshes current collector authority and the exact source proposal.
It writes two private independent repositories, preserving the base commit and
tracked source bytes. Ignored files, Git configuration, hooks and ancestor
objects are not copied. Known tracked credential filenames, redirected paths,
changed source and excessive sizes refuse preparation. The filename guard is
not a general secret scanner.

The preview discovers supported existing checks without executing them, verifies
the actual inverse/forward patch in the private proposed copy, and returns
`command_keys`, `affected_workflows`, `base_root` and `proposed_root`. Inspect
`discovery.json` and `contract.json` in the handoff directory. If discovery cannot
qualify the application's checks, pass an explicit [command definition](application-checks.md)
with `--commands COMMANDS.json` from the first preview onward. Commands and their
bounds freeze with the source, destination and branch.

Prepare `CONSUMERS.json` mapping **every returned affected workflow** to the
existing command keys that actually exercise it. This remains an owner's
coverage assertion; a passing command does not independently prove coverage.
The coding agent can prepare this file from its inspected application knowledge.
Incomplete or unrelated workflow maps refuse execution. Then, under the actual
application-test authorization, repeat the same command:

```sh
cutback campaign finish --workspace ../private-campaign-review \
  --purpose-reference application-change-review \
  --coverage CONSUMERS.json --execute-local
```

Both frozen trees must pass. The ordinary runner uses the current Python/Node
runtime with bounded processes, time and output, and a minimal fixture
environment. It inherits no provider or collector credentials. It installs no
dependencies and supplies no OS sandbox; inspect commands and application code
before execution.

After checks, current collector confirmation/content authority is refreshed
again. A successful result has `status: REVIEWED` and a local review ref in the
private proposed repository. The original source, index and refs remain
unchanged. Inspect `proposal.json`, `checks.json`, `review.json`, `change.patch`
and `rollback.patch`. A synthetic confirmation still requires explicit
`--sample-only` on both invocations and remains sample evidence.

The ignored application journal binds the campaign, project, collector,
confirmation artifact, source, destination, branch, commands and coverage. At
most three nonbillable preparation attempts can recover an interrupted copy,
preserving incomplete files. A known complete check receipt can reconcile a
lost reply without another execution. A dispatched check lacking a receipt
stops; deleting the journal to retry defeats that protection. Failed checks
cannot become a review. Source, authority or intent changes require inspection.

For an explicitly authorized draft PR, continue with
[campaign publish](review-publication.md). Preview first. Publication verifies
the saved exact review, requires the checked base at the named remote branch
and transfers only the reviewed source/commit. `campaign finish` itself makes
no GitHub request, merges nothing and never changes traffic or deploys.

The independent [observed-workload campaign](../examples/workload-portfolio/README.md)
and [campaign/outcome sample](../examples/improvement-campaign/README.md) use this
public handoff rather than their own copy/commit/check glue. They use closed
synthetic native transports and establish engineering behavior only.
