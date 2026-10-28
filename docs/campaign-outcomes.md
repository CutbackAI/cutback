# From a reviewed campaign to later outcomes

Working source addition; consult [acceptance/publication state](progress.md). PyPI dev5 lacks this command. It connects an exact [campaign review](improvement-campaigns.md) to canonical task-cost snapshots and immutable improvement receipts. It makes no model call, applies no source change and changes no traffic.

## Run the independent journey

With a source-matched owned local collector and authorized encrypted storage:

```sh
uv run python examples/improvement-campaign/sample.py --local-target . --outcomes
```

The script creates an independent native-SDK classifier and owned synthetic examples in the target's ignored workspace. Ordinary workflow preparation freezes the source/data; native structured generation, validation and untouched confirmation use canonical PostgreSQL reservations. Two separate Git workspaces run the application's SDK check with intercepted HTTP. The exact diff is reviewed and encrypted before upload. A deliberately cheap-but-worse later synthetic task window must return **REVIEW_ROLLBACK**, including generator, validation and confirmation costs.

The original application stays unchanged; only the proposed workspace contains the evaluated source change. Answers, token usage, later business outcomes and release linkage are fixtures, not measured model quality, customer savings or a real deployment. Two application-check SDK calls are separate from campaign calls and also use in-process transports. No vendor key or call is needed. Add `--cleanup` to delete this synthetic project. The collector has its own lifecycle command.

Open **Task economics → Compare frozen releases and optimization costs → Saved economic comparisons** to inspect the receipt. Opening saved history is read-only and scoped to the current project/environment.

## Link an application release

After an operator's separately authorized application release, freeze a later [task-cost snapshot](task-economics.md). Use its ID and the reviewed encrypted change's ID:

```sh
cutback campaign observe --candidate-snapshot LATER_SNAPSHOT_UUID \
  --change REVIEWED_CHANGE_UUID --release-linkage-attested
```

The agent reuses the saved original baseline, exact application-check review and every retained campaign run. The attestation declares that these releases correspond to the exact reviewed change; it does not independently verify deployment. Without it, the command stops before network access. The change must match both the reviewed diff hash and confirmation run. Missing retained context, altered reviews, incomplete campaign charges and another destination stop the link.

Unknown costs outside the campaign stay unknown. Pass these declarations only when the owner has confirmed their completeness:

```sh
cutback campaign observe --candidate-snapshot LATER_SNAPSHOT_UUID \
  --change REVIEWED_CHANGE_UUID --release-linkage-attested \
  --all-optimization-runs-declared-complete \
  --other-optimization-costs-declared-complete \
  --forecast-successful-tasks 10000 --minimum-tasks 30
```

With no supplied other-cost items, the second completeness flag asserts none exist. For nonzero or unknown training, compute, engineering or other costs, use a reviewed `--assumptions FILE` matching `ImprovementAssumptions` instead of declaration flags. Future volume is a forecast, not measured savings. Minimum samples and permitted absolute success-rate loss are explicit. No flag authorizes inference or deployment.

A private request identity is written before sending. Repeating identical inputs reconciles the same immutable receipt after a lost acknowledgement. An explicit fresh `--observation-id UUID` requests a new calculation after late cost settlement; the previous receipt stays frozen. Retention cannot be repaired by reconstructing missing runs/snapshots from a summary. Patch uploads are separate: reconcile an uncertain upload through Changes rather than blindly uploading another copy.

## Interpret the next action

- **REVIEW_ROLLBACK:** declared outcomes regressed; inspect sample size, failures, linkage and rollback before an operator decides.
- **REVIEW_COST_REGRESSION:** declared complete windows show no modeled cost-per-success improvement.
- **COLLECT_MISSING_EVIDENCE:** sample, outcome, cost or forecast coverage is insufficient.
- **PREPARE_INDEPENDENT_CONFIRMATION:** descriptive gates passed; causal savings and independent customer quality remain unproven.

The response always reports no action or traffic-change authorization. This is an explicitly invoked follow-up; continuous monitoring, automatic rollback and complete deployment inventory remain unimplemented. [Online incident rules](online-monitoring.md) are a separate workflow.
