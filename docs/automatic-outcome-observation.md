# Automatic outcome observation

Status: working source under qualification. Not in PyPI `0.1.0.dev5`. See [progress](progress.md) for executed acceptance and publication gates.

After a separately authorized application release, Cutback can check later task declarations under one finite approved policy. The agent reuses its reviewed campaign, original frozen baseline and canonical optimization cost family. The server derives their exact hashes. A metadata worker freezes closed windows and records a next review action without inference, traffic changes or deployment authority.

## Try the independent example

With a source-matched owned local collector, migration `0018` and explicitly authorized encrypted storage:

```sh
uv run python examples/improvement-campaign/sample.py --local-target . --outcomes --watch
```

The example creates its own synthetic native-SDK application and data. It runs ordinary agent preparation, native structured search with intercepted HTTP, untouched confirmation, application checks in two Git workspaces and encrypted source review. It then approves one metadata observation, waits for a real closed 60-second window, invokes the owned worker and reads the same agent status receipt. Two baseline successes versus one success and one failure in the cheaper later fixture produce `REVIEW_ROLLBACK`. The policy stops after that one check. No vendor key, paid call or application deployment is used. Add `--cleanup` to delete the synthetic project.

Open **Task economics → Automatic change observations**, inspect the policy hash and observation history. Rates describe producer-declared outcomes; they are not independently verified quality or causal savings.

## Prepare and approve a real policy

First finish the [reviewed campaign](improvement-campaigns.md) and separately authorize any application release. A policy requires an original complete, single-workflow/service/release baseline and an uploaded change matching that campaign review. Choose explicit candidate release linkage, nonoverlapping windows, late-arrival grace, minimum sample assumptions and bounded expiry. Times must include a UTC offset.

```sh
cutback campaign watch prepare --change REVIEWED_CHANGE_UUID \
  --candidate-release YOUR_RELEASE --release-linkage-attested \
  --first-window-end 2026-11-01T01:00:00Z \
  --expires-at 2026-11-08T01:00:00Z \
  --window-seconds 3600 --interval-seconds 3600 \
  --grace-seconds 300 --max-windows 30 --expected-tasks 100 \
  --assumptions reviewed-assumptions.json
```

Use your actual dates and reviewed assumptions. `ImprovementAssumptions` is the same contract as [manual outcome comparisons](campaign-outcomes.md). Unknown task populations or other costs stay unknown. Do not assert completeness because a field is inconvenient.

Review the returned immutable specification and approval hash. A current project administrator approves that exact hash with its real reference:

```sh
cutback campaign watch approve --approval-hash EXACT_SHA256 --approval-ref YOUR_REVIEW_REFERENCE
cutback campaign watch status
```

The private journal under the application's ignored `.cutback/agent/` directory is written before submission. Repeating identical preparation reconciles the same request after a lost response. The agent checks destination, project, environment, source hash, reviewed run family, policy hash and window evidence hashes. Status returns at most twenty windows, newest first; `--after INDEX` reads older retained windows. A changed source/release/assumption requires a new reviewed policy rather than mutating approval.

The dashboard can also prepare a policy from baseline/change selectors. It derives lineage server-side, preserves request identity after an uncertain response, and provides exact-policy approval, pause/resume and read-only history. Viewer credentials cannot create or approve policies.

## Worker operation and evidence limits

The worker is disabled by default. On an owned operator-managed installation, enable `CUTBACK_OUTCOME_WATCH_EXECUTION=true` on the worker; Compose forwards that explicit opt-in. Helm uses `execution.outcomeWatches=true` and requires an enabled worker. This metadata phase needs neither a model key nor encrypted content access. Existing content/experiment permissions are separate.

Each transaction freezes a service-scoped candidate snapshot, comparison where evidence exists, window receipt and durable counters. Concurrent ticks cannot duplicate a window. Lost commit acknowledgement is reconciled from persisted state. Busy project locks are skipped within a bounded twenty-project selection. After downtime, only the latest eligible closed window is processed; skipped intervals remain visible. It does not burst through historical windows. Late declarations received after the frozen cutoff do not rewrite history.

Policies expire within thirty days of the first eligible check and permit at most one hundred windows. A withdrawn approving principal, changed/missing baseline or source lineage, quota or invalid approved hash stops processing. Resume preserves counters; exhaustion is not reset by request replay. Explicit pause takes the same project lock as processing. A mid-calculation principal revocation is checked again before commit. No observation action licenses an automatic rollback, inference retry or new experiment.

Missing tasks/unknown coverage/insufficient samples request more evidence before a regression decision. Known declared outcome regression recommends rollback review; known cost regression recommends cost review. Passing descriptive checks requests independent confirmation. These labels never establish causality, production savings or independently successful tasks. The full canonical optimization family is charged once in each comparison; different windows are alternative descriptive comparisons, not additive savings.

Project export includes policies and retained windows; project deletion removes them. Retention prunes old receipts without resetting processed/skipped counters, expires policies and preserves a bounded approved baseline while its policy is active. An explicitly deleted policy does not erase its separately retained snapshots/comparisons. Populated migration downgrade refuses until an operator exports and explicitly deletes observation data. Released migrations `0001`–`0017` stay unchanged.
