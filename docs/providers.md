# Provider compatibility

The source-only [agent episode executor](agent-evaluation.md) separately qualifies OpenAI 2.29.0 Responses/Chat and Anthropic 2.3.0 Messages for synchronous, nonstreaming text/function conversations with declared synthetic fixtures. Every turn uses the durable experiment ledger. It supports no real application tools, parallel calls, generative simulator, agent stream/async surface or additional provider/model. This does not widen the observer matrix below.

Unreleased prompt-registry experiment contracts use the same pinned clients: OpenAI 2.29.0 Responses/Chat preserve declared roles and qualified JSON output options; Anthropic 2.3.0 maps initial system messages to native system blocks. Local Chat now executes its declared bound messages/options instead of ignoring them. A configured local server/model still needs its own acceptance. These synchronous text experiments add no tool execution, streaming replay or new provider/model support claim. [Prompt contract limits](prompt-releases.md).

| Runtime surface | Tested version | Exercised behavior |
|---|---|---|
| OpenAI Responses create | 2.29.0 | sync/async/native streams, retries/errors/outages, usage/cache/reasoning, privacy |
| OpenAI Chat Completions create | 2.29.0 and 1.55.3 | sync/async/native streams, per-attempt retries/logical linkage |
| Anthropic Messages create | 2.3.0 | sync/async/native streams, separate five-minute/one-hour cache usage |
| Text experiment executor | OpenAI 2.29.0, Anthropic 2.3.0, or configured local Chat endpoint | approved providers, bounded input/output, no tools/retries, pre-call reservations |

These are tested versions, not newest-version claims. `cutback[openai]` pins 2.29.0; `[anthropic]` pins 2.3.0. An application already pinned to 1.55.3 installs `[instrumentation]` alongside its existing pin. Legacy compatibility also tests Pydantic 2.10.6.

Explicit init uses service/environment, configured HTTPS endpoint ending `/v1/ingest/events`, project UUID and a scoped ingestion token. `providers=("openai", "anthropic")` or `CUTBACK_PROVIDERS` selects adapters. Default init is disabled. Initialize after worker fork; untested SDKs/existing resource wrappers are refused and the application's bootstrap must fail open.

OTel context is attached without replacing a global tracer. Wire export is versioned JSON, not OTLP. Native results, streams, exceptions and request options are preserved. Convenience parse/stream managers, raw-response wrappers, retrieval/background/WebSocket APIs, Azure, Gemini/Vertex, Bedrock, LiteLLM, embeddings, tools and arbitrary wrappers remain unsupported.

Streams are observed lazily. Missing final usage is unknown/unpriced; an abandoned stream may produce no event. Cutback does not change usage-in-stream options. Each observed retry attempt has a distinct event/attempt ID and shared logical operation ID. Failed attempts may be billable and are not zero-priced.

Export is background-only, bounded, with no disk spool, redirects or automatic event retries. Saturation, shutdown, network failure and cloud freezing can lose observations. A bounded flush returns at its wait deadline while an in-flight daemon export may continue. Real Lambda lifecycle acceptance remains required.

Metadata is the default. Nonstream text capture additionally requires active policy/vendor/environment/workflow scope, sampling/quota, redaction and encrypted storage. `CUTBACK_CAPTURE_DENY=true` stops SDK capture; server revocation/global capture disable reject new samples. Stream content capture is not advertised.

Complete supported text usage is priced conservatively. OpenAI input includes cached reads; Anthropic input excludes cache reads/writes. Reasoning is a subset of output. Ambiguous cache duration, absent usage/rates, custom endpoints, regional/tier/tool/modality units remain unpriced. Anthropic rates use [official pricing](https://platform.claude.com/docs/en/about-claude/pricing).

All ordinary inference is synthetic. No live provider or customer savings validation is claimed.

## Node observer and optimization adapters

The independent source package `@cutbackai/sdk` targets Node 24+, OpenAI 7.43.0 and Anthropic 1.3.0. Native promise/response types, sync-compatible async methods, retries, cancellation, lazy SSE bytes, bounded health/export and collector-outage behavior are exercised against actual installed SDKs with synthetic transports. It does not capture payloads or use ambient proxies. Custom provider hosts and untested SDK versions pass through without observation. Source/tarball delivery is not an npm publication claim.

The Anthropic experiment executor supports explicitly approved five-minute/one-hour prefix caching and reserves write premiums. Byte-bounded routing selects one declared target and freezes the worst target reservation; it does not introduce an LLM classifier, fallback or unapproved provider. Incomplete outputs cannot pass quality gates and unpriced usage remains unknown.

The explicit SDK `timeout_seconds` setting (0.01–2 seconds, 0.3 default) applies to both metadata and authorized capture HTTP requests. `flush` still shares one caller deadline across queues, and neither path retries dropped exports. A slow collector can therefore lose capture without changing the native provider result; local health reports distinguish captured/exported/dropped counts.
