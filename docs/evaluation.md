# Datasets, budgets and evidence

Tiers: 0 opportunity; 1 technical contract; 2 historical-reference alignment; 3 judge proxy; 4 independent held-out labels/tests; 5 controlled production evidence. A judge preference never verifies semantic correctness. No tier-5 result is fabricated.

Actual local corpus retrieval uses a separate [v6 execution contract](retrieval-optimization.md): frozen encrypted corpus, native answer-quality comparison, measured CPU/context components and separately approved untouched confirmation. Earlier declared retrieval fixtures keep their existing meaning. Missing infrastructure cost is never reconstructed from token usage.

## Evidence

`cutback datasets discover --target .` indexes fixture paths without reading/uploading content. Imports require a project-admin policy with approval reference, rights basis, environment/workflow/provider allowlists, expiry, sampling, byte/sample limits and retention. The server capture switch and encrypted store must also be configured. Redaction is defense in depth, not deidentification proof.

Dataset imports contain workflow/environment/policy, source_type/provenance_ref, independent_labels/representative attestations, split_seed and cases. A case has id/input, optional gold/reference, customer group and stratum. Old model outputs are references, not gold. Synthetic evidence stays synthetic and cannot establish representative customer verification.

Inputs, references, labels, prompt variants and rubrics are encrypted separately. PostgreSQL keeps safe pointers/hashes/provenance/metrics; purpose-scoped admin content reads are audited. Provider keys stay in the worker, never the browser.

Import infers a reviewable task hint from label shape: independent structured gold suggests field F1; repeated short labels suggest classification/exact accuracy; references remain provisional; ambiguous prose stays unknown and needs explicit criteria. No LLM or label text is sent for this inference. The automatic planner uses the hint only with a declared threshold and independent labels; otherwise it retains technical-only checks.

Frozen hashes include content and provenance. Normalized duplicates are unioned with customer groups before seeded stratified train/validation/test assignment. Rare groups/missing splits are flagged. This is selected near-duplicate detection, not universal semantic deduplication. Training examples never become held-out test evidence.

For frozen text request templates, source qualification also checks the actual rendered native inputs across the entire current dataset, before sampling, budget reservation or provider dispatch. Differences in unused record fields cannot create independent examples. Collisions merge existing families for paired confidence and required criteria; they never split a declared customer family. A merged family spanning train/validation/test rejects the plan with `NATIVE_INPUT_FAMILY_SPANS_DATASET_SPLITS`. Supply task-level customer groups and import a new dataset version; frozen assignments and approvals are never rewritten automatically.

This bounded check allows at most 32 MiB of rendered projection work. Rendering and input signatures remain in memory; public receipts contain only counts, the check version and its scope. Malformed templates or excess work fail before billing. Recommendation review repeats the check, so older template-based results without a matching receipt need new confirmation. `cross_dataset_rendered_input_history` stays `unknown`: the existing persistent lineage tracks normalized raw records and declared customer families, not every historical rendering under different templates. Episode/tool execution and retrieval keep their separate evidence contracts. This check neither supplies semantic deduplication nor establishes otherwise unknown population coverage.

## Plan, approve and run

```sh
cutback datasets import PROJECT_UUID dataset.json
cutback experiments plan PROJECT_UUID DATASET_UUID gpt-4.1 classify --max-usd 1 --max-requests 100 --minimum-accuracy 0.9 --output plan.json
cutback experiments submit PROJECT_UUID plan.json
cutback experiments approve PROJECT_UUID PLAN_UUID PLAN_HASH approval-reference
cutback experiments run PROJECT_UUID PLAN_UUID
cutback experiments report PROJECT_UUID RUN_UUID
```

Defaults authorize zero dollars. Without independent labels/criteria, automatic plans use technical nonempty checks and cannot verify semantics. Check provider, context, format/tool requirements and sample scope before approving candidates. Custom frozen plans can contain prompt variants.

Approval authorizes the exact hash for one run; queueing consumes it. Worker inference is disabled by default. Enable `CUTBACK_EXPERIMENT_EXECUTION` only with approval and a distinct `CUTBACK_EXPERIMENT_OPENAI_KEY`, or `CUTBACK_LOCAL_INFERENCE_ENDPOINT`/optional local key. Ambient application keys are never borrowed implicitly. Missing credentials block a queued run without a provider call.

The shared ledger reserves maximum modeled cost/request count before baseline, candidate and judge calls. Supported text input bytes/framing/output limits bound reservations. Unknown prices/providers/regions/capabilities are refused. No hidden retries or production tools execute. Wall/request limits apply; errors, cancellations, lost responses and crashed leases retain reservations. Lost leases fail rather than repeating a potentially billed operation.

The cap covers modeled inference under the frozen supported rate contract, not invoices, taxes or infrastructure. Local compute uses the approved customer upper-bound assumption. Unsupported actual usage stops execution. Reports distinguish known subtotals, incomplete spend and reservations.

## Metrics and changes

Exact labels include hashed confusion labels. Extraction uses field F1/TP/FP/FN; malformed/missing outputs contribute failures/false negatives. Schema/nonempty checks do not become semantic accuracy. External schema references and untrusted regexes are refused; collector plugins do not execute arbitrary evaluators.

Repeated runs retain variation and latency. Confidence resamples whole duplicate/customer groups with a fixed seed; exact labels also use Wilson bounds. Small/uniform-set bootstrap intervals can be weak: inspect independent groups/strata/failures. Verification requires predeclared thresholds/regression, adequate samples/groups, independent labels, representativeness and test split. Monthly savings stay unknown without defensible volume/coverage.

Optional judges use approved vendors/data and the same budget, freeze the rubric hash, blind/randomize answer positions and report invalid votes. Results remain NOT VERIFIED without independent evidence; production decisions need customer calibration.

Reviewed recommendations generate source/commit/hash-checked model diffs and rollback. Dynamic/provider/prompt migrations need inspected custom diffs. Canary logic reports guardrail/rollback decisions without changing traffic.

## Native request and source binding

`workflow prepare` constructs a baseline from a qualified parse-only request profile. Static instructions, supported native message/input template, structured-output options and output cap enter the encrypted frozen plan. Public metadata carries canonical hashes and source/config/prompt dependency hashes. All rendered native input and schema bytes enter the conservative input bound before the durable reservation or network call. Source-bound plan registration requires a matching project scan. A model-only diff requires exact source/request and unchanged candidate instructions/options/output cap; imported model constants are supported with explicit affected-workflow warnings.

Old or standalone unbound evaluations remain useful evidence, but cannot authorize source patches. Newly added canonical fields change outstanding old plan hashes after schema validation; recreate/reapprove them rather than mutating stored approvals. A generated proposal is still human-review pending. Shared configuration consumers outside the evaluated workflow have unknown quality until separately qualified. See [agent workflow](agent-workflow.md) for the resumable preparation and exact one-run approval path.
