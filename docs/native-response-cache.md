# Explicit encrypted native response caching

This source-checkout component is under qualification. It does not exist in the
published `0.1.0.dev5` wheel. Run the [independent local lesson](../examples/native-response-cache/README.md)
to see four requests use the actual SDK, PostgreSQL and encrypted collector.
Its closed synthetic transport makes no paid provider requests.

The optional `cutback.native_cache` adapter accepts a `NativeCacheScope` and a
pinned synchronous `OpenAI` client. `create(client, **request)` returns a
`NativeCacheResult`: `response` is the native SDK object and `receipt` describes
where it came from. On a miss, the returned response is the original object.
On a hit, the adapter reconstructs the corresponding pinned native response.

## What must be explicit

The application supplies its scoped collector token, project and capture policy,
a private request HMAC key of at least 32 bytes, workflow/environment, prompt
revision, freshness reference and TTL. Both content-retention authority and
immutable-or-safely-invalidated authority must be the boolean `True`. A similar
string or integer does not authorize capture. Keep this material out of source.

The cache binds the complete canonical request, pinned SDK version, provider
account and native headers inside an HMAC. Input and instruction text do not
enter lookup metadata. The collector checks current scoped credentials, policy,
environment, provider, expiry, content restrictions and shared payload quota.
Responses are stored only in the explicitly authorized encrypted store.

Supported requests are synchronous stateless plain text Responses, `store=False`,
literal bounded output and a fixed native provider credential. The client must
have zero retries and the qualified native destination. Streams, async clients,
tools, conversation state, custom gateways and modern reasoning/cache-write
contracts require separate qualification. The SDK support matrix is unchanged.

## Failure and accounting semantics

Lookup authorization, availability or schema failures stop before native
dispatch. A native exception propagates without replay. After a successful
native call, an uncertain/rejected/conflicting cache write returns the response
with its write status. Retrying that billable operation automatically is unsafe.

The client declares and checks the collector's exact-cache wire contract on
every lookup/write. An older strict API rejects the extra contract field before
storing a response. A changed write contract after native success remains
uncertain and never triggers another native call. A live entry with unavailable
ciphertext stops; it is not treated as a genuine TTL miss. The wire marker does
not change existing authenticated cache identities.

An expired entry can refill its existing shared-quota slot. A live entry stays
immutable until invalidation or a new revision. Concurrent refreshes commit one
replacement; misses are not coalesced. Ciphertext is removed only after a fresh
reference check proves it unreferenced. Pending transactions or an unreadable
database retain ciphertext for operator reconciliation; encrypted expiry still
blocks reads. Invalidation/expiry rollback never deletes a referenced response.

`native_sdk_calls` is zero on hits and one on native dispatch. It does not prove
the physical request count of an application-supplied transport. Cached usage is
historical, including any unknown fields. Never price it as another provider
bill. Invoice-verified cost stays unknown. Hit ratios in a synthetic lesson do
not establish production eligibility, whole-task cost, quality or savings.

## Agent experiments and source review

The working extension adds an explicit `--exact-cache-contract` JSON input to
native `workflow prepare --search` or `--automatic`. It freezes rights, immutable
freshness, TTL and an exact-decimal infrastructure upper bound. It reserves one
of five candidate slots for caching, repeats the declared sequence twice and
reserves worst-case provider plus cache-operation costs before each logical
operation. No observed repetition, unavailable capacity, unsupported request
shape or uncertain cache writes can produce a qualified cache recommendation.

Validation and separately approved untouched confirmation use different empty
namespaces. Synthetic repeat counts do not establish production hit rate. The
durable call ledger preserves cache origin, cost basis and historical model
identity; a cache delivery has no fresh provider response-model attribution.
Migration `0031` leaves old cache coverage unknown and refuses lossy downgrade.

After confirmation, the same-file named-client Responses source adapter prepares
an exact diff and rollback. It verifies the complete module AST and the ordinary
canonical recommendation, lineage and source bindings. Application source stays
unchanged. Unsupported or ambiguous consumers require a separate adapter.

The proposed helper preserves the original application behavior until its owner
sets `CUTBACK_NATIVE_CACHE_SCOPE_FILE`. Cutback imports are lazy on that path.
The private versioned JSON envelope contains `contract` (the approved typed
cache contract) and `scope` (`NativeCacheScope`). Its workflow, request revision
and contract digest must match the public hashes in the source artifact; TTL
and freshness must match the contract. Use a scoped ingest credential and a
fresh `secrets.token_urlsafe(48)` HMAC key, stored only in the application's
ignored private workspace. No provider credential belongs in this file.

Explicitly enabling an invalid or unavailable configuration stops; it never
falls back to a second native request. The enabled path uses a zero-retry client
copy, `store=False` and a bounded native dispatch while keeping the application's
shared client open. The runtime needs a source-matched Cutback installation.
Actual HTTP latency, availability, representative task quality and deployment
remain separate acceptance requirements.

This extension is under package/release qualification. Unified automatic
selection across cache, learned routing, retrieval, prompt generation and
training remains unfinished in the [market baseline](market-baseline.md).

## Executed component checks

The corrected contract and complete agent/source/runtime/browser scope passed
130 tests with one skip on each pinned SDK: 181.05 seconds on 2.29.0 and 185.98
seconds on 3.35.1. Old collector replies and unavailable live ciphertext stop
before native dispatch. Package, image and hosted qualification follow these
source checks; this scope does not establish production workload eligibility.

The current component scope passed 54 checks on OpenAI 2.29.0 and 54 on 3.35.1.
These cover native response fidelity, SDK outage/no replay, uncertain writes,
provider-account separation, privacy, real PostgreSQL migrations, shared quotas,
revocation, concurrent refresh and removal rollback/lost acknowledgement. Type
checking passed across 354 modules. The direct worker backend reuses those
services and checks the current approving principal and worker authority on
every operation; it does not create internal HTTP credentials. This is component evidence, not acceptance
of the remaining automatic optimizer.
