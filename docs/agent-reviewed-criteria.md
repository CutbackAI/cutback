# Let the agent prepare checks without approving its own work

This source-checkout extension connects reviewed criterion generation to the resumable application agent. It starts with the application's exact native source request and permitted labeled examples. The agent prepares a one-call generation plan, pauses for exact budget authorization, reads proposed checks privately for review, freezes validation, and prepares a separate untouched-test confirmation. It never approves generated criteria on the owner's behalf. These commands are absent from the published PyPI dev5 release; consult [acceptance status](progress.md).

For a no-key walkthrough, first use the [independent synthetic application](../examples/reviewed-criteria/README.md). The sequence below uses a real configured worker and can incur paid requests only at explicitly approved execution steps. Preserve application dependencies and keep `.cutback/` ignored. [Collector setup and rights](agent-workflow.md) apply throughout.

1. Run `cutback optimize . --json`. Inspect the qualified workflow and existing labels. Prepare its unexecuted validation basis with `cutback workflow prepare --local --search --example cases.jsonl --rights-basis owner_authorized --rights-reference REF --max-usd CEILING --max-requests LIMIT`. Set the owner's actual `--minimum`, `--min-test-samples` and `--independent-labels` only when justified. For an Anthropic target, add `--criteria-generator-provider openai` only when those training examples are authorized for that vendor. Do not execute this original basis.
2. Put the generation request below in a protected ignored file, filling in the owner's requirements and budget. Run `cutback workflow criteria-prepare --request-file .cutback/criteria-request.json --purpose-reference REF`. It reads the authorized basis and freezes a separate one-call plan without inference. Numerical requirements cannot weaken required checks in the basis.
3. Inspect `cutback workflow status`. Under authorization for that exact generation plan, use `cutback workflow continue --plan-hash HASH --approval-reference REF`. For the owned local collector, `cutback workflow work` executes only that saved approved generation run. A separately configured collector worker can execute it instead. `workflow continue` reconciles the same run after an interrupted reply; it does not replay an uncertain provider operation.
4. When status requests review, use `cutback workflow criteria-read --purpose-reference REF --output .cutback/criteria-review.json`. Inspect the prior required checks, proposed checks, unchanged thresholds and training anchors. This file contains private material. The session contains only opaque identity and hashes. Approve or reject the exact read version with `cutback workflow criteria-review --version-sha256 SHA --decision approve --approval-reference REF --purpose-reference REF`. Rejection stops this branch. Approval freezes validation; it does not approve its budget.
5. Authorize the returned validation hash and ceiling separately, then use `workflow continue`, the configured worker or owned-local `workflow work`, and `workflow continue` again. Inspect failures, sample sufficiency and costs. Choose one passing cheaper candidate. `cutback workflow confirm --candidate KEY --max-usd CEILING --max-requests LIMIT --purpose-reference REF` freezes the untouched-test plan. Its separate budget needs authorization before execution. Do not change criteria or recycle consumed holdout groups to obtain a pass.
6. After completed confirmation, use `cutback workflow gate --candidate KEY` and `cutback workflow proposal --candidate KEY --purpose-reference REF`. A sample-only demonstration cannot establish independent customer quality. The gate checks current criteria approval as well as the historical report. Withdrawn approval, expired content, changed source or insufficient evidence blocks the change. Review the exact diff and rollback, then execute actual application checks in clean original/proposed trees before considering an application release.

Example request shape; values are synthetic examples, not recommended customer thresholds or spending permission:

```json
{
  "requirements": {
    "task_brief": "Classify owned synthetic tickets using independently labeled categories.",
    "minimum": 0.9,
    "max_regression": 0,
    "min_test_samples": 30,
    "min_test_groups": 3
  },
  "generator": {
    "key": "criteria-generator",
    "provider": "openai",
    "model": "gpt-4.1-mini",
    "max_input_tokens": 24000,
    "max_output_tokens": 2048
  },
  "budget": {"max_usd": "0.10", "max_requests": 1},
  "max_training_examples": 4,
  "transport": "native_provider"
}
```

Preparation saves an opaque request identity before submission. Repeat the same request after a lost draft response; changed requirements cannot reuse that identity. Queue recovery reads the existing generation run. Review/validation and confirmation preparation similarly reconcile the same nonbillable artifact after lost replies. A different candidate or budget cannot replace a partially frozen confirmation. Restarting source analysis retains an active criteria checkpoint. `workflow status` states the next action; an uncertain billable operation remains stopped for reconciliation. The private nonce bookkeeping file is required to reconcile validation preparation and must stay with its session.

The [criteria contract](reviewed-criteria.md) limits generated checks to deterministic metrics; it does not generate semantic judges or independent ground truth. The agent cannot infer business thresholds, vendor permissions, meaningful labels, representativeness or customer savings. Generation overhead remains separate from evaluation costs.

Engineering acceptance exercises a fresh independent application against real PostgreSQL, the installed native SDK and an owned HTTP transport. It injects lost draft/queue/validation/confirmation replies and withdraws reviewed approval after confirmation. Run `python scripts/check.py tests/integration/test_agent_reviewed_criteria.py tests/integration/test_criteria_draft_reconciliation.py -q` against the documented disposable PostgreSQL setup. These tests make no paid inference calls.
