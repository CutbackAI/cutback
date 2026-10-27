# Investigate an entire workflow

Source-checkout capability, not included in PyPI `0.1.0.dev5`. Apply additive migration `0007` and rebuild the dashboard when updating an existing collector. Released migrations `0001`–`0006` are unchanged. The scanner remains offline and imports no server or tracing SDK.

Cutback now has two separate observation planes. Native LLM events retain the pinned provider accounting contracts. Workflow spans retain safe execution structure for agent, chain, retrieval, reranker, embedding, tool, prompt, guardrail and evaluator steps. A generic LLM span does not assert a provider, token count, price, replay capability or business outcome.

## Try the independent sample

For the simplest first run, use `uv sync --locked --all-extras --dev` and `uv run cutback demo` in a source checkout. Select **Explore sample**, then **Investigate a failed session**. The local demo seeds a failing and a reference execution, encrypted synthetic content and external scores alongside its existing native-SDK comparison. Open a trace, compare its path with the other trace, and use an explicit audit purpose before reading its permitted content. This source-only increment is not in the published dev5 demo.

From a source checkout, follow the [local quickstart](quickstart.md), create a staging project, and issue an **ingest** key through Security. Set `CUTBACK_PROJECT_TOKEN` in your local process environment; never paste it into a committed file or command argument.

```sh
python examples/workflow-tracing/sample.py --dry-run
python examples/workflow-tracing/sample.py --endpoint http://127.0.0.1:8080
```

The dry run prints only generated synthetic metadata. The second command sends one synthetic delayed task result and a four-step trace to your collector. It makes no inference request. Connect to that project in the dashboard, open **Traces**, choose **Browse sessions**, then open its trace. `lookup.order` has an execution error; the external `order-state.correct` check failed. Execution status and that externally asserted task judgment are separate. Cost and population coverage remain unknown.

The [sample](../examples/workflow-tracing/README.md) includes an actual producer fixture generated with OpenTelemetry SDK/proto/export encoder `1.45.1` and OpenInference semantic conventions `0.1.42`. This qualifies the documented JSON projection only; it does not qualify every instrumentor, Phoenix export format, provider or transport.

## Metadata contracts and privacy

`POST /v1/ingest/spans` accepts `profile: cutback.workflow.v1` and 1–200 spans. Fields are trace/span/parent/session identifiers, operation identifier, kind, service, optional workflow/release, start/end nanoseconds and explicit status. A missing end represents incomplete timing. Native JSON ingestion requires integer timestamps; returned timestamps are decimal strings to preserve nanoseconds in JavaScript. New arrivals must start within 30 days and end no more than five minutes in the future. Unknown status stays `unknown`; `ok` describes execution, not task success. Clock skew between related spans remains visible rather than changing timestamps.

The credential determines project and environment. Project readers can select configured environments; ingestion keys cannot read investigations or write another environment. Project administrators can configure an environment before issuing its scoped key. Every request uses shared authorization and rate quotas; accepted metadata also follows the existing optional hosted-ingestion cap. No optional hosted service is required for OSS use.

Only identifier fields are accepted. Use reviewed opaque identifiers, never user text, document names or personal data as labels. Arbitrary attributes, inputs, outputs, arguments, exception text, rationale, headers and application payloads are forbidden in this plane. Metadata ingestion does not authorize capture. The separate opt-in content boundary below reuses the existing encrypted store.

Each project/environment/trace/span tuple is immutable. An identical retry reports a duplicate. Different content at the same identity returns `409 SPAN_IDENTITY_CONFLICT_BATCH_ROLLED_BACK`; the whole batch rolls back, including otherwise valid rows. This intentionally does not silently replace an unfinished span with a later different record: send completed spans once, or use a new span identity for a separately observed operation.

## Reuse OTLP JSON / OpenInference

`POST /v1/ingest/workflow-otel/v1/traces` accepts the bounded [OTLP HTTP JSON](https://opentelemetry.io/docs/specs/otlp/) envelope with **explicit projection** `openinference-metadata.v1`. It is separate from the earlier [priced GenAI-only importer](otel-ingestion.md).

Retained resource attributes: `service.name`, `deployment.environment.name` and legacy `deployment.environment`. A supplied environment must match the key. Retained span attributes: `openinference.span.kind`, `session.id`, `cutback.operation`, `cutback.workflow_id`, `cutback.source_commit`. The first two follow [OpenInference conventions](https://arize-ai.github.io/openinference/spec/semantic_conventions.html). Missing operations get a fixed kind label; span names are never used as labels. Unsupported kinds become `unknown`.

Other attributes are discarded and counted in the receipt; span names, status messages, scope names and envelope metadata are discarded. Duplicate retained attribute keys reject the resource/batch or reject the affected span with explicit partial success. Invalid spans produce bounded `partialSuccess.rejectedSpans` without echoing values. Identity conflicts remain atomic `409`. Nonempty events/links, unsupported attribute shapes, protobuf and gRPC are outside this profile and must be removed/converted by the producer. Request size is at most 1 MiB, with at most 200 spans. This endpoint never creates priced native events or enables replay.

OTLP nanoseconds accept decimal strings or JSON integers. Unlike standard protobuf JSON bytes, OTLP JSON identifiers are hex. Use a JSON-capable, reviewed exporter/collector branch; Python's normal OTLP protobuf exporter cannot be redirected to this JSON-only endpoint unchanged.

## Inspect structure without inventing completeness

Trace/session lists use environment-scoped keyset pagination, ordered by immutable receipt time and identity. New sessions and traces appear first. A cursor that was deleted by retention or belongs elsewhere returns a sanitized error; restart from the first page. List filters include execution errors and, on traces, exact workflow/service/release/native-model identifiers and offset-aware start/end windows. Step filters match one retained step; model filters require an exact native observation trace identity. Session records can contain multiple traces; a trace may explicitly join more than one session through its spans.

An investigation returns at most 1,000 retained spans and 200 external results. It displays missing parents, missing/multiple roots, cycles, parent interval violations and truncation. A root or orphan arriving later changes the observed graph without rewriting spans. The waterfall shows observed intervals; overlapping children are not added together as elapsed time. Coverage is always explicit unknown because dropped or uninstrumented work cannot be inferred from a connected tree.

LLM correlation uses exact project/environment/trace/span identity. Retained native attempts take precedence over an external mirror. Multiple native matches at one span are ambiguous in its detail; their cost is not guessed. Cost and usage reports also exclude retained OTLP mirrors when exact native observations exist. No matching by model name, time proximity or prompt similarity occurs. Existing daily aggregates cannot be reconciled after raw identities expire. The investigation exposes only the known cost subtotal of its returned LLM observations; full-trace cost remains null and generic tool/vendor cost remains unknown.

## Delayed external results

Select another trace in the same environment to compare retained paths. Alignment uses exact operation/kind ancestry and repeated-operation occurrence order. Cycles, missing ancestors and ancestry beyond 32 levels remain unmatched. Each side is bounded to 500 spans with visible truncation. Different paths or faster execution are not automatic proof of better task quality.

`POST /v1/ingest/evaluations` accepts `profile: cutback.evaluation.v1` and 1–200 immutable results. Supply a UUID result identity, source, evaluator/version, offset-aware observation time, exactly one trace or session target, and optionally a span for a trace target. Results can precede their targets. Identity scope includes project and environment. Identical retries are duplicates; differing reuse atomically rejects the batch.

Score types are boolean, bounded decimal numeric, categorical identifier or none. Outcomes distinguish `passed`, `failed`, `error` and `unknown`. Errors require a safe error code and no score; unknown results cannot carry a score. Missing results remain absent, not passed. Decimal scores preserve precision; NaN/infinity and malformed shapes are rejected. Free-text rationale is rejected. The trace/session displays `externally_asserted`, the source/version and whether a span target is in its retained page. None of these scores satisfy Cutback's independent confirmation gates automatically.

## Retention and acceptance

`POST /v1/ingest/trace-content` is a separate, explicitly authorized route. A scoped ingest key may attach bounded input/output strings to an existing span only under a currently enabled policy permitting its environment, workflow, content-provider scope, sampling rate, byte/sample quota and expiry. Content is redacted under the current policy, encrypted, and linked through a sixth metadata table; PostgreSQL does not receive the input/output strings. A different record cannot overwrite the same span's content. Failed relational writes clean up the encrypted object. Content-provider scope is an authorization declaration, not token or billing evidence.

Project administrators can use the span's **Authorized encrypted content** control to make a purpose-scoped, audited read. The server checks the current policy and expiry on every read. Viewers cannot obtain it by calling the read route directly. The UI escapes text, hides content on request, keeps credentials/content in memory, and never interprets content as HTML. Captured output is a reference, not gold. Existing payload-expiry workers delete encrypted objects; project deletion deletes objects before removing their pointers.

Workflow spans use each project's raw-retention cutoff. Empty traces, session links and empty sessions are removed; surviving children visibly retain missing-parent uncertainty. External results expire by receipt time at the same raw-retention boundary. Metadata export includes all six new tables, with safe content pointers and no captured strings. Project deletion cascades their rows. No trace latency distribution or complete cost is reconstructed after pruning.

Executable checks: `tests/integration/test_workflow_traces.py`, existing OTLP/operations/retention tests, migration drift and real upgrade/downgrade, `web/unit/trace-layout.test.ts`, and authenticated `web/tests/traces.spec.ts` through the real-PostgreSQL dashboard fixture. They cover synthetic producer projection, concurrency, duplicates/conflicts, late results, role and environment boundaries, malformed/content fields, parent cycles/skew, 1,000-span truncation, exact cost correlation, export/delete/retention, navigation, loading/empty/error/retry, mobile and axe accessibility. See [progress](progress.md) for the actual latest run outcomes rather than treating a test filename as a pass.
