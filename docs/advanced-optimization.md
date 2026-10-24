# Explicit optimization strategies

All strategies are Apache-2.0 source. Ordinary scanning, metering and self-hosted reports require none of them. A proposal does not grant deployment permission or prove a customer's semantic quality.

## Native model and prompt comparisons

Frozen experiment specifications support supported native model swaps, independently reviewed `instruction` variants, repeated paired evaluation, and optional blinded judging. Raw instructions/rubrics/labels/schemas are encrypted; metadata retains their hashes. Every call, including repeats and judges, consumes a durable pre-call reservation. A changed price/evaluator contract requires a new plan and approval. Unknown or incomplete billing cannot produce a verified cost comparison.

Anthropic model specifications can explicitly set `prompt_cache` to `anthropic-5m` or `anthropic-1h` with a static `instruction`. The native Messages adapter sends the corresponding ephemeral system-prefix cache control. Worst-case reservations include cache-write premiums. Returned cache read/write tokens determine modeled cost; requesting caching does not establish a cache hit, latency benefit or savings. OpenAI prefix caching remains provider controlled and is observed through reported cached input usage. No unsupported cache retention option is silently invented.

## Validation pilot and early elimination

Set `pilot_samples` and an explicit `pilot_minimum` to evaluate a bounded, stratified set of independent validation groups first. This is available only for a final test-split plan. Candidate pilot calls are included in the frozen worst-case request/USD estimate and use the same pre-call ledger. A candidate failing the declared metric threshold or technical checks is eliminated before full held-out calls; if all candidates fail, the unnecessary baseline run is skipped. Unknown billing after a pilot outage remains unknown. Results distinguish validation diagnostics, eliminated candidates, held-out comparisons and total experiment spend. No pilot result is relabeled as a test-set quality claim. Zero pilot samples preserves the ordinary paired evaluation path.

## Deterministic routing

An experiment candidate can use `provider: "router"`, a policy name in `model`, and up to eight increasing UTF-8 byte bounds. Each route has a complete native `target` model specification. For example:

```json
{
  "key": "length-router",
  "provider": "router",
  "model": "reviewed-input-size-policy-v1",
  "routes": [
    {"max_input_bytes": 1024, "target": {"key": "short", "provider": "openai", "model": "gpt-4.1-mini"}},
    {"max_input_bytes": 4096, "target": {"key": "long", "provider": "openai", "model": "gpt-4.1"}}
  ]
}
```

This uses input size as a declared routing rule, not a claim that short inputs are semantically easy. Routing targets must pass the same provider, model, price, instruction and token-bound checks as ordinary candidates. The plan's provider allowlist and data policy must include every actual target. Nested routers and routed judges are refused. An input outside all approved bounds fails before a provider call. Each input invokes exactly one target; no classifier call, shadow traffic or automatic fallback is hidden in routing.

The ledger reserves the most expensive permitted target before execution. Results expose actual routed model counts, provider usage or declared local compute bounds, paired quality evidence and unknown hosting/compute overhead. A routing rule is frozen in the approved plan hash. After evaluation, a customer can use `route_target` in its own reviewed application integration; Cutback does not deploy routing or blindly rewrite application types. The ordinary model-literal patch generator refuses provider/router changes that need a custom source review.

## Encrypted application cache

Set `CUTBACK_CACHE_ENABLED=true` only after approving the integration. The collector must also have explicitly enabled encrypted payload storage and an active policy covering the application workflow, environment, provider, data rights, expiry, quota and retention. Metadata-only observation never enables caching.

The application calls `POST /v1/ingest/cache/lookup` and `POST /v1/ingest/cache/put` with its scoped ingestion key. Keys include project/policy, workflow/environment, exact provider/model, prompt revision, freshness reference and an application-computed SHA-256. The application must hash **all** behavior-affecting inputs, user/tenant/authorization context, relevant settings and knowledge/data revisions. The server cannot infer whether the application omitted a meaningful dimension. Do not share keys across authorization boundaries or cache mutable answers without explicit invalidation.

Writes require `immutable_or_safely_invalidated: true`, a response and a TTL. They are immutable: reuse returns a conflict; invalidate the existing entry or change the revision. Responses are encrypted under the project-bound artifact codec, never stored in event metadata. Payload byte and object quotas are shared with dataset/sample capture. Viewers cannot read cached responses. Tenant/environment mismatch, policy revocation and expiry deny access immediately. Cache lookups perform no inference; the caller decides what to do on a miss. They do not automatically replay a crashed billable operation.

Administrators can list metadata and editors can invalidate individual entries. The worker deletes expired cache objects in bounded batches. Database TTL enforces sub-day freshness; encrypted envelope retention has day granularity and physical cleanup follows the worker cycle. Project deletion deletes registered cache objects before removing references. Customer backup/object version history follows the operator's separate expiry policy. Cache hit rates or realized savings are not fabricated from static repetition or a cache-control request.
