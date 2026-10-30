# Native provider batches

Source implementation under qualification; this is not in the published PyPI dev5 wheel. Start with the independent owned example: `uv run cutback demo --provider-batch`. Add `--ticket-router` for the separate agent-driven instruction search. Docker, Git and the repository's pinned server/OpenAI dependencies are required. No provider key is needed and inference stays inside the synthetic HTTP transport.

The batch example has 240 tickets, three categories and 60 declared intent families. It evaluates 48 validation cases per model through the actual native SDK, reserves every item before submission, collects out-of-order results and deletes known provider files. Answers and token usage are synthetic. This establishes lifecycle behavior, not real model quality or savings.

## Approve one execution

Create an ordinary encrypted plain-text validation plan with a supported OpenAI reference/candidate, input/output ceilings, deterministic metric/criteria and dollar/request budget. Open **Provider batches**, select the pending plan and explicitly authorize provider storage. Preparation shows the frozen hash, models, ceilings and budget. A project administrator approves that exact contract once. It consumes the plan; the ordinary experiment queue cannot execute it again.

The first contract supports the existing qualified GPT-4.1 / GPT-4.1-mini Responses text profiles and April 14 snapshots. It does not expand availability or override provider deprecations. It refuses request profiles, routing, tools, multimodal inputs, judging, source bindings, test holdouts and retained case outputs. Other approved engines keep their separate contracts. A batch validation cannot authorize a source patch or claim independent test quality.

The worker requires authorized encrypted storage and `CUTBACK_BATCH_EXECUTION=true`; it uses `CUTBACK_EXPERIMENT_OPENAI_KEY` in memory. API handlers and CLI commands never invoke the provider. Enabling a worker does not replace approval. Keys never belong in plans, browser URLs or persisted metadata.

Every item uses the existing project-locked execution ledger. Reservations cover the full online native upper bound. Canonical receipts identify the batch and its distinct frozen price hash. Final modeled costs use native usage and the qualified batch discount; missing usage, unqualified units, mismatched models or malformed files preserve uncertainty. These are modeled public prices, not invoices or complete application economics.

## Storage, bounds and time

The provider completion window is 24 hours; Cutback binds a separate owner-selected 24–26 hour turnaround limit. This differs from the synchronous plan's local processing limit. Content rights must cover the entire window.

The plan's local wall allowance is a cumulative active-processing budget across worker steps; waiting between polls is excluded. Each active step reserves at most 180 seconds before work and returns unused time only after a confirmed completion. A crashed step keeps its conservative time charge. Accounted, uncertain and currently held processing microseconds remain separate. Exhaustion stops new execution. Containment, authorized late-cost reconciliation and deletion have separate bounded steps and cannot authorize further inference or a quality claim. An already admitted network request can finish after its deadline; its result remains subject to the current accounting and authority checks.

Batch processing retains data at the provider. `store=False` on a Responses request does not make batch storage eligible for zero data retention. Cutback requests 48-hour file expiry, explicitly deletes known input/output/error files and records confirmations. Idempotent cleanup can retry safely. Local inputs/instructions use current encrypted-store policy, quota, expiry and deidentification checks. Metadata export includes batch state. Project deletion refuses unresolved vendor-file cleanup.

Cutback bounds one job to 1,000 native items, six single-model batches, 8 MiB input per file, 16 MiB downloaded results per file, 32 MiB combined results and 256,000 bytes per JSONL line. Opaque reservation UUIDs replace customer case IDs. Duplicate IDs/JSON keys, foreign IDs and inconsistent item counts reject reconciliation before a prefix can settle. Missing rows remain uncertain. Result order does not matter.

The dashboard shows job turnaround, descriptive validation metrics, frozen group counts and native cost coverage. Per-request latency and quantiles remain unknown. CPU, invoice adjustments and other application costs are not inferred from tokens.

## Cancellation and recovery

Both batch controls and ordinary experiment cancellation reach the same containment state. Completed work can be charged after cancellation. With active rights, the worker records late known charges and marks undispatched items not started. Revocation stops payload downloads; charges may remain unknown while known vendor files are deleted. Cancellation cannot become a passing quality result.

Upload and submission intent precede their native requests. A crash or lost acknowledgement never automatically repeats either operation. **Find submitted jobs without resubmitting** performs bounded read-only lookup using the opaque operation identity. An interrupted upload must match the original filename, byte count and SHA-256 content before submission can continue. A submitted job must match the exact input-file and operation identities. No match is not proof of no file or zero charge. Duplicate matches and incomplete lookup preserve uncertainty. Each lookup checks at most 500 recent objects.

An administrator can provide a known input-file or batch-job identifier through the dashboard recovery form or CLI `--hints recovery.json`. The file contains `{"hints":[{"model_key":"baseline","file_id":"file-EXISTING"}]}` or the corresponding `batch_id`. Only the interrupted frozen phase is eligible. Native metadata and input content are checked before use; foreign identifiers cannot authorize new work. Cancellation can reconcile identities for deletion without reading payloads or starting inference. Preserve the original operation and held charge while investigating uncertain work.

CLI equivalents:

```console
cutback batch prepare PLAN_UUID --project PROJECT_UUID --endpoint http://127.0.0.1:8080 --provider-storage-approved --output batch.json
cutback batch approve BATCH_UUID --project PROJECT_UUID --endpoint http://127.0.0.1:8080 --approval-hash EXACT_HASH --approval-ref reviewed-budget --output approval.json
cutback batch show BATCH_UUID --project PROJECT_UUID --endpoint http://127.0.0.1:8080 --output status.json
cutback batch cancel BATCH_UUID --project PROJECT_UUID --endpoint http://127.0.0.1:8080 --output cancellation.json
cutback batch reconcile BATCH_UUID --project PROJECT_UUID --endpoint http://127.0.0.1:8080 --output reconciliation.json
```

Use the existing authenticated control-API credential configuration. Never put keys in these files. Loopback HTTP is for an owned local collector; remote servers retain the existing HTTPS requirements.

[Batch lifecycle/pricing](https://developers.openai.com/api/docs/guides/batch), [provider data controls](https://developers.openai.com/api/docs/guides/your-data), [GPT-4.1](https://developers.openai.com/api/docs/models/gpt-4.1) and [GPT-4.1-mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini) were inspected October 30, 2026. Actual paid-provider, customer and production qualification remain external gates. None was performed for this increment.
