# Compare actual context compression against the original task

Source-checkout capability. The published dev5 package does not contain these commands. Install the matching source, collector and dashboard; see [the sample](../examples/context-compression/README.md) and [executed acceptance](progress.md).

The compression journey keeps the native model, instruction, output ceiling and supported native request configuration fixed. It compares the application's original context with exact duplicate-line removal and query-based extraction of whole lines. These are extractive hypotheses, not semantic-equivalence guarantees. A shorter context that loses the answer fails the same task metric and required criteria as any other candidate.

## Complete workflow

1. Import rights-cleared query/context examples and independent answer labels under an active encrypted content policy. Each `input` is a JSON string with exactly `query` and `context`. Gold answers and evaluator references stay outside the native input.
2. Inspect the application's clean tracked `compression.json` with `cutback compression inspect-config`. It binds the exact settings file, entire source tree, commit and compression runtime recipe. Settings-consumer coverage remains declared; this does not discover every dynamic consumer.
3. In **Compression**, select the dataset and optional inspection receipt, then prepare the comparison. Cutback proposes at most three distinct settings: exact duplicate-line removal and query-based selection at half/quarter the original byte allowance. Explicit API candidates support up to five alternatives. Preparation makes no provider request.
4. Review and approve the exact native API budget and request ceiling. Queue one run on the normal operator-enabled worker. Every physical native call uses the canonical durable pre-call ledger. A lost queue response is reconciled by reading the same plan execution; an uncertain billable call is never automatically replayed.
5. Inspect actual answer scores, native usage and measured local CPU. With explicitly authorized case retention, an audited administrator can read the original example, actual compressed native text frame and response. Context-byte reduction is not a tokenizer measurement. Missing native usage or a missing CPU cost basis cannot authorize an economic selection.
6. Freeze one passing, lower-component-cost validation candidate on untouched test groups. Confirmation has a separate exact approval. It preserves the dataset, settings, native model, evaluator, CPU assumptions and input bounds. A failed confirmation cannot reopen search on those consumed groups.
7. Read the authorized settings proposal. `compression patch` creates an exact reversible diff without applying it. Run the application's checks on independent clean base/proposed trees, then `compression review` binds the precise checked settings change and declared consumer coverage. An optional local review branch never overwrites another ref. Nothing is pushed or deployed.

The source-built `cutback demo` seeds an owned synthetic context application, a passing duplicate-line alternative and an aggressive alternative that loses an essential fact. Its native SDK responses and usage are synthetic. This demonstrates the product journey, not actual model accuracy or customer savings.

A settings binding proves which file and source tree a proposed diff changes. It does not prove that every application's dynamic request builder uses that setting, model or frame. The declared native request and application-consumer checks must match the actual application; unsupported builders require an inspected adapter. A passing fixture or settings-only check is not universal source/runtime coverage.

## Frozen algorithm and limits

`cutback.extractive-context.v1` is a core-only pure function: no file, network, SDK, model or application execution. Its hash binds the frame, algorithm and Unicode normalization runtime. The application and evaluator use the same function.

The input is bounded to 64 KiB of JSON, 4096 UTF-8 query bytes, 32768 context bytes and 2048 lines. A plan can impose smaller query and context limits. The existing native input ceiling must cover conservative JSON escaping and framing; oversized identity/deduplication inputs are rejected rather than truncated. Query selection ranks whole exact lines by distinct normalized query-term overlap, breaks ties by original position, skips lines that do not fit and restores original order. It never silently shortens a query or a line. Unmatched and adversarial text can cause bad selections; independent task checks must detect those failures.

`deterministic-v7` requires an explicit compression execution contract and nonzero content nonce. Existing v3–v6 serialization and approvals omit the new optional field and remain unchanged. The initial native surfaces are the already-qualified synchronous nonstreaming OpenAI Responses and Anthropic Messages text executors. Native tool/episode execution, provider migrations, learned/generative compression and arbitrary application transformations require separate qualification.

Before sampling or billable dispatch, actual compressed native inputs are grouped across the complete frozen corpus. Existing raw/customer families only merge. Collisions across frozen splits reject; repeated compressed inputs cannot inflate independent evidence. Renderings and projection signatures remain in bounded RAM, never metadata. Cross-dataset rendered-input history remains unknown.

The run clock starts before compression/grouping. Preparation checks the same deadline and current content authority. A bounded RAM cache avoids recompressing identical inputs for every repeated call, and its CPU accounting includes whole-corpus lineage preprocessing. Context-byte totals describe distinct preparations, including undispatched lineage inputs; they are not dispatched token totals.

## Costs, privacy and recovery

Native costs and uncertainty use the existing canonical ledger. Local thread CPU is measured separately and priced only under an explicit exact Decimal USD/CPU-hour assumption, rounded up to the approved charge quantum. Unknown is different from declared zero. This covers native API and declared compression CPU components; memory, storage, idle infrastructure, human review and complete business-task cost remain unmeasured.

Metadata reports contain settings, counts, timings and cost coverage. Query/context text, transformed inputs and retained outputs remain in the authorized encrypted content plane. Existing project/environment scope, RBAC, current-policy checks, expiry, quotas, export and deletion apply. Untrusted context never authorizes a tool, file read or URL fetch. These controls do not make this extractor a general application guardrail or injection defense.

API paths are `POST /v1/projects/{project}/compression/draft`, `POST .../compression/confirmation` and the administrator-only `POST .../compression/{confirmation_run}/proposal`. Approval, queue, cancellation, execution reconciliation and reports use the ordinary experiment APIs. `cutback compression --help` covers source inspection, draft, confirmation, rendering, proposal, patch and checked review. Offline `render` reads explicitly chosen local files and writes content to the explicitly selected output; the scanner does not read context automatically.
