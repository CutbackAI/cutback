# Provider and SDK compatibility

| Integration | Static scan | Runtime evidence in this revision |
|---|---|---|
| OpenAI Responses `create` | Import/constructor aliases, sync/async source | SDK **2.29.0**, sync/async, nonstream/stream, synthetic HTTP contract tests |
| OpenAI Chat Completions `create` | Same | SDK **2.29.0**, sync/async, nonstream/stream, synthetic HTTP contract tests |
| Anthropic Messages `create` | Direct/aliased constructors, imported clients | Runtime adapter not implemented; price unknown |
| Azure OpenAI | Warning for recognized constructors | Not supported; regional/deployment accounting pending |
| SDK factories, `self.client`, dynamic imports | May be unresolved or missed | Not automatically attributed |
| Direct HTTP, LiteLLM, Gemini, Bedrock, frameworks | Narrow direct-HTTP warnings; no coverage claim | Not supported |

This is the **tested version**, not a claim that it is the newest SDK. `uv.lock` pins every tested dependency. Initialization rejects a different OpenAI version and an already wrapped resource method; it does not try to guess compatibility. Native SDK decorators are distinguished from external wrappers by implementation source path. This detection is a safeguard, not proof that arbitrary third-party observation stacks cannot duplicate events.

OTel trace/span context is attached when present. No global provider is replaced. A new random span ID is metadata for the observed operation; it is not exported as an OTel SDK span. JSON protocol translation is independently versioned; no OTLP support is advertised.

Only explicit SDK resource `.create` methods are hooked. `.parse`, `.stream` helper managers, raw-response wrappers, WebSocket/background retrieval, internal retries as individual attempts, and wrapper frameworks require separate compatibility evidence. Retried calls currently produce one operation record. The event's retry number stays null. This is a release blocker for complete attempt accounting, not an exactly-once attempt claim.

For streaming, the native iterator is observed lazily; chunks/results/errors are passed through. Final usage is recorded once when iteration completes or the stream is closed. Early closure without final usage is incomplete/unpriced. An abandoned stream that is neither exhausted nor closed may produce no event. Cutback does not enable `include_usage` or change request options on the developer's behalf. Chat streams without usage remain unknown.

Text-only compatible calls can use the small bundled price seed. Tools, modalities, nonstandard endpoints, regional/deployment/tier variants and absent cache usage are unpriced. Reasoning tokens are a subset of output; cached input is subtracted from inclusive OpenAI input. No raw response/usage object or provider error message is serialized.

Live OpenAI/Anthropic validation has **not** occurred. Ordinary tests use only synthetic fixtures and no real provider credentials.

