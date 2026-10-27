# Reuse an existing OpenTelemetry pipeline

Cutback accepts a deliberately bounded **OTLP/HTTP JSON GenAI metadata profile** at `POST /v1/ingest/otel/v1/traces`. It uses the existing scoped ingestion key, shared quotas, PostgreSQL transaction, idempotency and reporting path. The key determines the project and environment; neither comes from an untrusted project identifier in the body. This is an optional alternative to native SDK observation, not a general tracing backend.

Source checkouts also provide a separate [whole-workflow metadata projection](workflow-tracing.md) for generic agent/tool/retrieval spans and sessions. Its endpoint, contracts and unknown-status semantics are distinct from this older priced GenAI profile. It is not yet in PyPI dev5.

The wire shape follows [OTLP 1.11.1 HTTP/JSON](https://opentelemetry.io/docs/specs/otlp/) with the [GenAI attribute registry](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/). Trace/span IDs use hexadecimal strings; 64-bit timestamps accept decimal strings or JSON integers. Up to 200 completed spans and 1 MiB are accepted per request. Empty exports succeed. Unsupported provider/non-GenAI spans produce explicit `partialSuccess.rejectedSpans`; malformed or unsafe metadata rejects the entire batch without retaining any span. JSON only: protobuf, gRPC, metrics, logs, arbitrary span events and links are outside this profile.

## Configure the producer

Export only approved GenAI metadata through a filtered branch of your existing pipeline. Keep your ordinary tracing backend. Prefer one source per call. Source checkouts exclude retained external mirrors from cost reports when exact project/environment/trace/span native identity exists; different IDs cannot be reconciled, and legacy daily summaries cannot be deduplicated after identities expire. Existing SDK wrappers are deliberately refused by native initialization; OTLP reuse avoids that conflict.

Use an HTTPS destination and `Authorization: Bearer SCOPED_INGEST_KEY` from your runtime secret manager. Local development permits loopback HTTP. Configure the complete traces URL above, or an OTLP HTTP/JSON base endpoint ending in `/v1/ingest/otel` when your exporter appends `/v1/traces`. Confirm the actual exporter protocol and destination in your own environment; an SDK that only exports protobuf needs a reviewed JSON-capable collector/exporter branch.

| Resource attributes | Meaning |
|---|---|
| `service.name` | A bounded metadata identifier; otherwise the source is labeled external-otel |
| `deployment.environment.name` | Must match the ingestion key; legacy `deployment.environment` is also accepted |

| Span attributes | Meaning |
|---|---|
| `gen_ai.provider.name` | `openai` or `anthropic`; legacy `gen_ai.system` is accepted, conflicting values are rejected |
| `cutback.sdk_surface` | Required: `responses.create` or `chat.completions.create` for OpenAI, `messages.create` for Anthropic |
| `gen_ai.request.model`, `gen_ai.response.model` | Bounded model identifiers; unresolved/unsupported rates remain unknown |
| `gen_ai.operation.name` | Optional operation metadata; never used to guess a native API surface |
| `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` | Optional nonnegative integers; never rounded from floats |
| `cutback.usage_source` | Explicit `provider_reported`, `counted`, `estimated` or `unavailable`; missing provenance leaves token fields null |
| `cutback.metering_status` | Explicit `text_only`, `unsupported_units` or `unknown`; missing metering stays unknown |
| `cutback.cache_read_tokens`, `cutback.cache_write_tokens`, `cutback.cache_write_1h_tokens`, `cutback.reasoning_tokens` | Optional counters with the native accounting invariants; missing values remain null, not zero |
| `cutback.workflow_id`, `cutback.call_site_id`, `cutback.source_commit` | Optional reviewed source identities; never derived from prompts |
| `cutback.traffic_kind` | `application`, `evaluation`, `judge` or `unknown`; absent purpose stays unknown |
| `cutback.logical_request_id` | Optional reviewed UUID linking spans to one operation |
| `cutback.result_status`, `http.response.status_code` | Optional bounded status; absent result status follows the completed span's OTel error status |

Remove all other resource/span attributes before sending. Prompt/message/system/tool payload attributes, arbitrary values, duplicate keys and nonempty events/links are rejected. Span names, status messages, scope names and unknown envelope metadata are never persisted. Error responses do not echo input. This path does not authorize payload capture or evaluation data rights.

Set provider-reported provenance only after verifying what your tracer actually records. A reported external span can aggregate SDK retries; it cannot prove per-attempt billing or unsampled population coverage. Reports expose `external_otel_observations`, retain the external sampling profile and warn about those limits. `observed_attempts` remains a compatibility field counting received observations; it is not a reconstructed retry count for external spans. Daily retention preserves the external-span count without inventing missing quantiles or coverage.

## Exercise the profile with synthetic data

After creating a project and scoped ingestion key, set `CUTBACK_PROJECT_TOKEN` in your environment and run `python examples/runnable/otel_json.py`. The example imports no Cutback internals, emits only labeled synthetic metadata, uses no provider key, refuses redirected credential forwarding and makes no inference request. Inspect the project's Costs and external-span warning. Replaying the same export is idempotent; two different projects' identical span IDs remain isolated.

This profile's parser, roles, environment denial, duplicate receipts, unknown provenance, content rejection, timestamp/ID bounds and partial success are tested against actual PostgreSQL. It does not certify every third-party exporter, agent client or observability stack. Qualify your actual filtered producer before enabling private application traffic.
