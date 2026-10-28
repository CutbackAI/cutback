# Build sequence after the market audit

Status: engineering sequence, 27 October 2026. Implementation is recorded per release below; the remaining roadmap releases are **not completed**. Existing tested functionality stays supported. [Baseline IDs](market-baseline.md) identify acceptance requirements; [differentiation](differentiation.md) identifies the outcomes to prove. There is no calendar estimate until the implementation and external acceptance effort are measured.

## Preserve the foundation

Keep the independent Apache-2.0 package, offline parse-only scanner, native provider adapters, PostgreSQL canonical state, encrypted customer content plane, durable one-shot cost reservations, scoped credentials and optional modules. Do not replace them with a hosted chat wrapper, SQLite telemetry demo or unbounded provider proxy. Preserve released migrations and add new ones. Never broaden an SDK support claim from a metadata importer alone.

New data models need project/environment scope, compound identity constraints, indexed query paths, deterministic/idempotent writes, bounded batches/windows, retention/export/delete, safe audit fields, concurrent mutation behavior and sanitized errors. New content belongs under the existing rights/purpose/expiry/quota boundary, not in arbitrary JSON metadata. New dashboards require generated API/types, a production React build, authenticated browser/mobile and accessibility acceptance. A worker/optimizer change needs crash and uncertain-charge tests, not just a successful mocked response.

## Release 1 â€” understand a whole workflow

Implementation: the bounded source-checkout workflow is now implemented; see [walkthrough](workflow-tracing.md) and [actual acceptance/publication state](progress.md). This does not close the later releases or qualify every exporter.

**Baseline:** F06, T02â€“T10, O05, O06. **User outcome:** open a failed session and identify its failing step without rebuilding the application around Cutback.

Implement a generic metadata span plane separate from priced native LLM attempt records. Retain trace/span/parent/session identity, operation kind, service/workflow/release, bounded start/end times and explicit execution status. Do not infer business success from an HTTP success or price unknown-provider spans. Correlate native LLM observations by exact identity; choose a canonical observation source instead of summing duplicates. Generic span insertion cannot grant native request replay support.

Start with a documented metadata projection of OTLP JSON and one qualified OpenInference producer fixture. Reject or explicitly project unsupported content; never silently store arbitrary attributes, names, exception messages, tool arguments or retrieved documents. Report duplicates/conflicts, partial success and profile version. Preserve missing roots, orphan parents, out-of-order arrivals, cycles and truncation as visible integrity issues. Add cursor-paginated trace/session lists and a trace tree/waterfall; do not calculate full-trace totals from a truncated page.

Add versioned external evaluation-result metadata with exact targets, source/evaluator version, score shape and outcome/error/unknown distinction. Attach delayed results idempotently; an imported score is externally asserted evidence, not an independently verified Cutback result. Keep free-text rationale in authorized encrypted content.

**Acceptance:** real PostgreSQL migration/upgrade/downgrade; cross-project/environment isolation; ingest/viewer/admin role matrix; late/duplicate/conflicting spans; clock skew/missing parents/cycles; bounded huge trace; same trace ID in two tenants/environments; unknown status/provider/cost; content rejection; retention/export/delete; canonical LLM correlation without duplicate spend; authenticated browser waterfall and empty/error/loading states. Ship an independent synthetic multi-step trace sample with no API key or paid call.

## Release 2 â€” turn failures into trusted regression data

Implementation: the bounded import/review/lineage workflow is implemented in the working source; [walkthrough and limits](imports-and-review.md), [independent example](../examples/review-workflow/README.md) and [acceptance/publication state](progress.md). Qualification of final archives and hosted checks remains required before publication. This does not implement Releases 3â€“7 or the scenario generator in A09.

**Baseline:** F07/F08, D05â€“D10, E07, A09. **User outcome:** select failures, distribute review, and create a maintained test dataset with provenance.

Add a versioned importer interface with qualified native JSONL plus initially selected Phoenix/OpenInference and Promptfoo/other evaluation-suite formats. Actual adapters need pinned real-format synthetic fixtures; merely accepting arbitrary JSON is not compatibility. Preview mapping, rejected fields and content rights before import. Preserve original source identity and externally asserted metrics; never relabel historical output as gold.

Add review queues, items, assignments and immutable review/adjudication records. Item state transitions: ready â†’ leased â†’ reviewed â†’ adjudication when required â†’ resolved; expired leases return safely without overwriting submitted judgments. Validate assignees against current project grants. Blind review is enforced by server response authorization, not only hiding a panel. Rubrics/required-reviewer counts freeze per queue version. Numeric averages do not manufacture consensus on categorical corrections.

Promotion creates a new frozen dataset version with exact reviewed inputs/labels and lineage. A corrected dataset does not mutate an already approved plan. Maintain cross-version group identities so repeated reimports cannot casually recycle a consumed final test group.

**Acceptance:** concurrent claims, lease expiry, user revocation, unauthorized peer-answer reads, disagreement/adjudication, immutable revisions, policy expiry/deletion, import preview/atomicity, malformed/oversized archives, no SSRF/file traversal/arbitrary evaluation code, source provenance, group leakage and real PG/RBAC/browser checks.

## Release 3 â€” prompt versions and application experiments

Implementation: the bounded registry/playground/alias and local-check journey is implemented and locally accepted; [contracts](prompt-releases.md), [portable checks](application-checks.md), [examples](../examples/prompt-releases/README.md) and [acceptance/publication state](progress.md). Hosted checks remain publication gates. Generated rubrics (P09), automatic shared-consumer scheduling (E09), tools and general conversation execution are still partial/later work; they are not implied by this increment.

**Baseline:** P01â€“P10, E06/E08/E09, F09. **User outcome:** author a change, compare it against declared application checks, and promote or roll back the exact version.

Add prompt identity/version/dependency/alias/promotion records. Encrypt prompt bodies and schemas; retain only safe hashes/references in relational metadata. Use a deliberately limited template language with explicit variables, no arbitrary Python/Jinja execution, no network/file includes and deterministic composition/cycle limits. Resolve dependencies by pinned versions. Alias promotion uses compare-and-swap/version checks and audit receipts. Read scopes must not bypass payload rights.

Playground drafts create frozen experiment plans under the same pre-call reservation boundary; the browser never receives provider credentials or silently runs inference. Compare known failure cases, permitted structured outputs and task slices before proposing a release. A source patch and a registry alias change are separate artifacts with separate rollback.

Add explicit local application-check contracts: exact command/working directory, permitted environment variables, no inherited provider/admin secrets, timeout/output limits and source provenance. Never execute application code in the scanner or shared collector. Check results are externally executed evidence with clear trust/attestation limits. Bind both base and proposed source trees for portable CI validation. Provide JUnit/machine-readable failure details without exposing content by default.

**Acceptance:** missing/extra variables, dependency cycles, concurrent versions/alias promotions, revoked content policy, escaped/XSS content, no automatic inference, budget/cancellation/uncertain charge, valid alternative outputs, shared-config regressions, stale/tampered artifact rejection, independent CI workspace and exact rollback.

## Release 4 â€” production quality and incident workflow

Implementation: the metadata/incident foundation and authorized content-quality extension are publicly merged. They include pinned native judging through the pre-call ledger, separate internal/external score provenance, encrypted opt-in judge responses and bounded observed cohort comparisons. See [monitoring](online-monitoring.md), [content quality and limits](online-quality.md) and [acceptance/publication evidence](progress.md). Cohorts remain descriptive; semantic drift, statistical significance, causation and population coverage are not inferred.

**Baseline:** O04â€“O10, R07, C10. **User outcome:** find a quality regression, triage it, and make it a regression case.

Version online rules by target/filter, evaluator, sample policy, window and total budget. Execute out of the request path unless explicit inline enforcement is configured separately. Use deterministic sampling/deduplication, durable backlog/leases, fairness and retention-aware backfill. Judge/vendor payload access needs explicit policy; metadata-only scores can be ingested without running a judge. Track missing/error/failed results separately, including unobserved denominators.

Add alert-rule and incident records with minimum samples, threshold/window, cooldown, deduplication, acknowledge/resolve/recovery and audited owner transitions. Provide internal UI/API alerts first. Optional webhook delivery needs endpoint allowlists/SSRF protection, signed payloads, bounded retry/dead-letter behavior and delivery status; configuring it, not merely building it, authorizes external messages. Shared saved views link evidence and owner without embedding credentials.

**Acceptance:** window boundaries/late scores, no-data versus healthy, evaluator outage, rule-version change, concurrent evaluators, budget exhaustion/uncertain calls, duplicate incidents, recovery, retention truncation, notification failure, tenant quotas and mobile/accessibility incident flow. No automatic production rollback unless separately authorized.

## Release 5 â€” evaluate agents, retrieval and state

Implementation: the bounded native fixture journey is locally accepted, including durable per-turn receipts, typed task/retrieval/state checks, encrypted episode revision, scenario drafts and independent support-agent/RAG dashboard samples. [Contracts and limits](agent-evaluation.md), [actual acceptance/publication state](progress.md). Semantic groundedness, generative personas, real tools and specialty execution remain explicit gaps; this increment does not close the entire market baseline or Releases 6–7.

**Baseline:** A01â€“A10, X01â€“X04 as qualified specialties. **User outcome:** test the complete task, including alternative tool paths and final state.

Define separate typed conversation, retrieval and tool-trajectory contracts. Add deterministic tool selection/argument/order/side-effect/final-state checks, retrieval relevance metrics with declared references, groundedness/citation checks and conversation constraints. No single metric becomes universal task success. Add adapters incrementally with native provider qualification and synthetic tools; preserve unknown token/price/modality values.

Keep application/tool execution isolated and opt-in with explicit fixture/read-only/sandbox permissions, maximum steps/time/requests and no ambient credentials. A simulator's messages and costs are not the real user's distribution. Freeze simulator/persona versions and separate generated training cases from confirmation. Run benign utility and adversarial task checks together. Integrate established red-team/guardrail tooling where qualified rather than claim a forbidden-word filter is an equivalent security product.

Voice requires audio transport, turn-taking/interruptions, acoustic conditions and measured timing; multimodal and browser/computer agents each need their own adapters/accounting/execution policies. Retain these in the backlog until accepted; text-only support must remain accurately labeled.

**Acceptance:** multi-turn ordering, tool-call/result pairing, alternative valid paths, duplicate state changes, malformed/refused/incomplete outputs, loops/handoffs/cancellation, retrieval labels/citations, malicious tool data, exfiltration/unauthorized action denial, simulator leakage and complete cost accounting. Publish independent support-agent and RAG examples with intentionally failing cases.

## Release 6 â€” automatic economically useful improvements

**Baseline:** C03â€“C10, G05/G06/G08/G09, X07/X08. **User outcome:** the agent prepares a useful change with less engineer effort and defensible total economics.

Add pluggable candidate generators/optimizers with a strict adapter boundary. Start with qualified prompt and parameter search, then workload-eligible caching/routing/retrieval/compression/training. Every proposer/evaluator/target/simulator call uses the canonical ledger; no optimizer may make hidden out-of-band calls. Translate external optimizer budgets into this envelope rather than treating an in-process callback count as durable dollar control. Log exact candidate versions and rejection reasons without leaking permitted examples.

Choose search effort from the workload and expected break-even opportunity; abstain when evidence/volume/cost is unknown or benefit is too small. Adaptively select on validation, freeze a selected candidate, then separately confirm on untouched groups. Prepare application checks, exact diffs, affected-consumer tests and a reviewable branch/PR. Never deploy from the optimizer.

Add task outcome and change-observation records joining application release, verified result and allocated costs. Show failed-task spend, incomplete coverage, one-time optimization costs and later measured windows. Full cost per successful task remains unknown when its components are incomplete. Subsequent windows can recommend rollback or re-evaluation under an existing policy, not silently authorize new spend or traffic changes.

**Acceptance:** cheap-but-worse candidate, search-cost payback failure, proposer/judge overhead, unknown tool prices, missing outcomes, source mutation, protected test reimport, cross-tenant artifacts, crash at each ledger boundary, reproducible application PR checks and later observed outcome linkage. Compare against a simple model downgrade and a qualified optimizer-only baseline before complexity is justified.

## Release 7 â€” broad platform and launch qualification

**Baseline:** remaining F/G/R/X rows and all core end-to-end journeys. **User outcome:** a new team can adopt, operate and recover the product independently.

Complete gateway interoperability and, where built, application virtual-key budgets/model policies/reliability under distinct authorization. Add declared load/SLO tests for ingestion, trace queries, online evaluators and worker fairness. Qualify supported storage/backup/identity/deployment modes and retention/export/delete across every new table. Keep SCIM, voice, GPU and other specialties visible until qualified or explicitly designated integrations with their own acceptance evidence.

Use separate release checks for installed wheels, hosted CI matrices, JS/native SDKs, generated frontend/API artifacts, independent examples, security/dependency scans, public source/history/archive boundaries and documentation links. Public release notes must distinguish newly shipped capabilities from source-only work. No new PyPI version is implied by a source merge.

Conduct observed first-use trials and the task-matched comparison in [differentiation](differentiation.md). Engineering prepares reversible work before external decisions. Customer interviews/outreach, real data/vendors/spend, public customer claims and production deployment remain owner-authorized external steps. They do not justify describing unfinished baseline engineering as complete.

## Completion record for each release

Record baseline IDs closed, exact source/SDK/tool versions, migrations, executable acceptance commands, passed/failed/skipped checks with reasons, browser evidence, known limits, package/publication state and remaining external gates in `docs/progress.md`. Keep failed/abstained trials visible. A release is complete only when its user journey and all required checks pass; the presence of endpoints or screenshots alone is insufficient.
