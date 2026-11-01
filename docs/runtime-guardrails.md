# Runtime guardrails

The bounded source workflow is accepted in [PR 48](https://github.com/CutbackAI/cutback/pull/48), with installed-package, image recovery, authenticated browser and hosted evidence in [progress](progress.md). Broker integration is separately accepted in PRs 49–50. It is not included in the published PyPI dev5 wheel. Existing scanner and passive observation remain independent; these literal/schema/tool-name rules do not establish semantic safety.

## Try the independent application

From the reviewed source checkout:

```console
python -m pip install -e ".[guardrails]"
cutback guardrails demo
```

The command starts its own read-only checker process and sends private configuration through an anonymous pipe. It runs a bounded synthetic JSON application, proves that a denied input never dispatches, quarantines a denied output after dispatch, and rejects an unapproved tool name. It removes its own process and makes no vendor requests. It does not provision a database or grant persistent application authority. See `src/cutback/sample_app/runtime_guardrails.py` for the complete independent application.

For the complete collector/native gateway/dashboard journey, install the source server and OpenAI extras, start Docker Desktop and run `cutback demo --runtime-guardrails`. The owned support-desk demo adds a separately launched checker, exact collector approvals and native JSON/SSE controls. Its four native synthetic calls and nine guard checks remain inspectable under Runtime guardrails. Keys are revoked and policies disabled when the synthetic execution finishes; these retained receipts do not authorize traffic. No provider credential is read and no inference vendor is contacted.

## Policy and authority

`GuardrailPolicy` binds a nonzero project, principal, policy ID, environment, workflow set, finite aware time window, explicit content consent and private content nonce. The nonce prevents public policy hashes from being direct hashes of guessable rules. Public review metadata contains scope, stage counts, bounds and the frozen hash; it contains no literals, JSON schema or rule names. Registration is immutable by policy ID. Changes require a new policy and exact approval.

Qualified rules are literal string denies (exact or NFKC case folding), a bounded structural JSON Schema subset, and tool-name allowlists. The schema subset excludes references, regex, external formats and executable validators. Literal matching is not a semantic prompt-injection detector. Tool-name admission grants no execution, field, tenant, network or SQL privilege. Existing isolated-tool permissions remain independently required.

The checker accepts bounded JSON over a fixed TLS origin, with a scoped hashed expiring token. Explicit plaintext mode requires literal `127.0.0.1` and an actual loopback peer. It has no provider, database, Docker, collector-admin or project-store credentials. There are no plugin callbacks, arbitrary imports, admin HTTP endpoints, redirects, environment proxies or HTTP retries. Requests remain in RAM; finite replay records retain keyed body identity and metadata only. Process-local CPU/rate bounds are distinct from the collector's shared check quota.

The operator can boot `python -m cutback.guardrail_boot --private-pipe`, delivering `GuardrailServiceConfig` through stdin and closing the pipe. Alternatively use an authenticated encrypted configuration bundle with a dedicated AES-256-GCM key and expected service ID. The bundle key is separate from the project payload master key. TLS certificate/key paths are operator configuration. Private rule content must never be written into an ordinary analytical record, public example, command argument or log.

## Collector controls

Use `/v1/projects/{project}/runtime-guardrails` with an administrator and current authorized encrypted storage. The registration JSON contains `service_id`, `capture_policy_id`, `policy`, and a finite `max_checks`. The content policy must cover every declared environment/workflow and the complete runtime window and retention period. Rule content consumes the same shared content quota as other encrypted artifacts. Redaction that would change rule semantics rejects registration.

Ordinary reads and export contain review metadata. A separate administrator-only purpose-declared content read is audited. Approval submits the exact approval hash, a new approval UUID and expected state version. Disable is terminal; a replacement requires a new identity and review. Viewer, ingest, cross-project and stale-state mutations are denied. Current approver rights, encrypted payload existence/expiry, consent and runtime scope are checked again at execution. Missing retained content cannot reconstruct authority.

`DurableGuardrailChecker` persists a check intent before HTTP, consumes a project-serialized shared quota slot, and persists its metadata decision before allowing dispatch or delivery. A preexisting intent cannot silently authorize redispatch. Missing database evidence, ended authority, scope errors and exhausted quotas deny even when checker outages were approved to fail open. Check statuses distinguish allowed, blocked and unavailable; reserved and unavailable evidence never becomes known complete coverage.

## Application integration

`GuardedWorkflow` / `AsyncGuardedWorkflow` wrap an explicitly reviewed function that accepts and returns bounded JSON. The wrapper snapshots the exact checked input and complete returned value, preventing later mutation from substituting unchecked content. It calls the function once, checks its output before returning it, and never retries. The function must not deliver output through another channel. Arbitrary native response objects and iterators are rejected; application code explicitly converts a native result to its intended JSON delivery value. This does not claim transparent streaming SDK interception.

`GuardrailRejected.dispatched` distinguishes a pre-call denial from an outcome after dispatch. Output denial cannot undo a model charge or tool state change. Unknown cost remains `None`; cancellation and uncertain writes never authorize an automatic retry. The `tool=True` mode requires both `tool_input` and `tool_output`; supply `{name, arguments}` on input and retain the underlying tool's independent permissions.

## Guarded native gateway

A distinct `cutback.gateway-policy.v3` binds the complete approved public runtime guardrail identity. Its time window fits inside the runtime policy, environment matches, and it declares one workflow and one qualified native model route. Released v1/v2 policy serialization and approval hashes omit absent guardrail fields. The separately operated gateway must have the matching fixed-origin scoped `AsyncGuardrailClient`; approval alone does not configure or deploy it.

The gateway boot profile accepts one configured runtime policy through `CUTBACK_GATEWAY_GUARDRAIL_BINDING_FILE` (public binding JSON), `CUTBACK_GATEWAY_GUARDRAIL_ORIGIN` (fixed TLS origin), and either `CUTBACK_GATEWAY_GUARDRAIL_TOKEN` or its `_FILE` secret-delivery alternative. Tokens stay out of the public binding. Both credential sources together, symlink/oversized files, private rule fields, wrong projects and malformed profiles are rejected with fixed errors. The token file is read into the client without copying its value into the ambient environment. Explicit `CUTBACK_GATEWAY_GUARDRAIL_ALLOW_LOOPBACK_HTTP=true` permits only literal loopback for an owned local test. The gateway needs `CUTBACK_ALLOW_PAYLOADS=true` to validate continuing content-processing authority; it needs no project payload master key and loads no private guardrail literals. The checker has its separate private configuration and cannot inherit gateway/provider/database credentials.

Input is the complete strict native request JSON. Output is an envelope containing `native_response`, `native_stream_events`, `native_wire`, `joined_text_deltas` and `joined_native_text`. A nonstreaming response has empty events/deltas and null wire; its native text/refusal/summary parts are joined independently to catch literals split across parts. For SSE the checker sees all parsed events, complete UTF-8 wire (including comments) and joined string deltas (including refusal deltas). Rule schemas must describe this envelope. Content/structure bounds can deny large responses even below the gateway's separate 4 MiB wire ceiling. Arbitrary encodings or concatenation rules in an unsupported consumer are not inferred.

Guarded SSE is fully buffered and checked before any native bytes are delivered. It preserves accepted original wire bytes and native SDK parsing, but adds latency and is explicitly marked `x-cutback-output-buffering: verified-before-delivery`. It does not report a buffered upstream delta as delivered first-text latency; that retained field stays unknown. `x-cutback-guardrail-coverage` distinguishes complete declared checks from explicitly permitted unavailable checks. Fail-open output can be delivered with unknown coverage only under the exact reviewed mode.

Canonical provider usage is settled before the output decision. A subsequent denial preserves known modeled cost, and lost settlement acknowledgements can repeat the same accounting receipt without repeating provider HTTP. Interrupted streams retain uncertain usage/reservations and deliver no partial buffered content. The qualified native model/provider surface is unchanged; no general model, semantic-safety, real customer or production performance claim follows.

## Remaining acceptance

Focused rules/service/client/boot/application tests and real PostgreSQL controls pass. Guarded gateway cases cover intent-before-dispatch, split deltas, raw wire fields, hidden JSON fields, outage modes, revocation after charge, interruption, and actual native sync/async JSON/SSE clients. Final broad installed-package, SDK3, dashboard browser/mobile/axe, recovery image, publication boundary and exact-head hosted gates remain open. Tool broker interposition, richer classifiers/third-party guardrail adapters and wider provider targets are separate unfinished requirements.
