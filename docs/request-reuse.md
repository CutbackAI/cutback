# Request reuse: measure before selecting a cache

Status: unreleased source implementation; acceptance is recorded in
[progress](progress.md). This extends native exact caching with an explicitly
authorized, encrypted request profile. It does not complete unified selection
among caching, routing, retrieval, prompt changes and training.

Default metadata observation remains unchanged. To profile, an administrator
must authorize `CapturePolicy.native_request_profile`, including literal `true`
for fingerprint capture and immutable or safely invalidated context, a freshness
reference and prospective TTL. The TTL cannot exceed authorized retention.
Field exclusions disable profiling rather than fingerprinting excluded content.

The SDK additionally needs explicit content capture and
`CUTBACK_NATIVE_REQUEST_PROFILE_HMAC_KEY`: standard base64 encoding of 32–128
random bytes, configured through the application's secret manager. Never put this
key, collector credentials or provider keys in a repository, report or dashboard.
The SDK sends a keyed request identity to the encrypted capture plane. The key
never leaves the process. A keyed identifier separates rotated keys; request
identities also bind the pinned native SDK, credential/account, headers and exact
request. Credentials use the native adapter's scope-bound PBKDF2 derivation.

Each profiled sample uses **two** approved storage-object slots: its ordinary
encrypted example and a separate encrypted profile. Combined plaintext bytes fit
the policy limit. Both pointers commit together, survive lost commit acknowledgements
and follow payload expiry, project deletion, backup and key rotation. A retry
cannot silently replace content or recreate an expired sample. Default event
metadata, reports and dataset cases contain no fingerprints. Metadata exports
exclude object references; aggregate analysis receipts contain no request keys.

## Qualified calls

Only pinned OpenAI **2.29.0 and 3.35.1**, plain synchronous `responses.create`,
official native destination, stable credentials, `max_retries=0`, `store=False`,
nonstreaming text and an explicit output cap of 16–16,384 tokens qualify.
Tools, async calls, streams, custom destinations and other unqualified calls
retain their ordinary SDK behavior and do not receive a usable profile. Native
exceptions and responses are preserved. No cache or additional provider request
is enabled by profiling. Profile key setup failure preserves ordinary capture.
Concurrent profiling does not wait on a busy derivation lock; skipped attempts
remain visible as missing evidence. The first credential derivation adds work;
actual application latency acceptance remains external.

## Run and inspect

Start with [the synthetic eight-call example](../examples/request-reuse/README.md).
For an existing authorized project, open **Request reuse** in the dashboard,
choose an approved profile policy, workflow, environment and completed window.
An administrator can queue at most 100 retained analyses per project. Each job
reads at most 1,000 profiles within a 5–300 second cooperative wall bound and
expires within one hour or earlier policy expiry. Storage driver timeouts bound
individual supported backend requests; this is not hard process preemption.

CLI equivalents are `cutback request-profiles analyze PROJECT configuration.json`,
`list PROJECT`, `show PROJECT ANALYSIS_ID`, `cancel PROJECT ANALYSIS_ID` and the
operator's `worker-once`. All network commands accept `--endpoint`. Configuration:

```json
{
  "request_id": "00000000-0000-4000-8000-000000000001",
  "policy_id": "00000000-0000-4000-8000-000000000002",
  "environment": "staging",
  "workflow": "classify",
  "purpose": "approved-cache-assessment",
  "window_start": "2026-01-01T12:00:00Z",
  "window_end": "2026-01-01T13:00:00Z",
  "max_observations": 1000,
  "max_wall_seconds": 60
}
```

Replace the illustrative UUIDs and timestamps; create one fresh request UUID
per intentional analysis and retain it for reconciliation. A window must include
an explicit UTC offset, end in the past and span at most one day. Repeating the
same exact submission returns the same job. Changing scope, purpose or limits
under that UUID conflicts. No uncertain submission is blindly retried with a
different identity. A worker claims once before storage reads; a crashed or
expired read lease fails terminally. Cancellation remains available after policy
revocation. Current principal, policy, scope, hash, content expiry and wall bound
are checked before each encrypted read and before publishing a summary.

No inference is requested, but remote encrypted storage requests may be billable.
Queueing an analysis explicitly authorizes these bounded storage reads. Failed
reads do not automatically replay. Expired derived summaries are hidden immediately
and pruned in bounded maintenance passes. A minimal one-shot identity receipt
remains, so the same request UUID cannot dispatch again after expiry. Project
deletion removes it. Backups follow operator policy.

## Interpret the report

The model sorts native monotonic start times, begins with empty cache entries and fills only
after an eligible cold response completes. Overlapping cold requests stay misses;
later completion cannot refresh an already live entry. Equality at a completion
boundary is ambiguous and blocks automatic selection. Rotated keys or changed
requests never join. SDK process clock domains stay separate; multiple independent
clock domains block automatic selection rather than assuming synchronized clocks.
Wall-clock timestamps scope the requested retained window; the same
`perf_counter_ns` source supplies native start and duration. The required
`process_perf_counter_ns_v1` clock basis prevents interpreting incompatible
snapshots as this timing contract. Completion and TTL stay within a domain.
TTL is the approved prospective TTL.

The denominator is retained canonical metadata attempts in the same project,
environment, workflow and window. Missing profiles, sampling, truncation, unknown
completion timing, conflicting events and unqualified responses block automatic
selection. A sampled stream cannot be scaled into a cache census. Calls lost
before metadata export and uninstrumented application traffic remain unknown;
even a complete retained stream does not prove population coverage.

`cache_trial_eligible` supports preparing a **separate** trial for the retained
stream. It is not authority to deploy a cache. Modeled avoided provider cost uses
exact Decimal values only for potential hits with known retained prices; any
missing hit price makes the total unknown. Cache transport/infra cost, whole-task
cost, invoice verification, semantic task quality, production hit rate and realized
savings remain unknown. Zero assumed cache latency is stated explicitly. Do not
substitute this report for native replay, independent quality validation,
confirmation, application checks and source-bound diff/rollback review.

## When the report declines

Missing key: configure the private application key and collect a new permitted
window. Missing attempts or sampling: inspect exporter health and capture limits;
do not infer that missing calls would hit. Changed/revoked policy, missing blob,
corrupt hash or outage: inspect current storage/authority and the saved terminal
receipt. Started uncertain reads do not replay. No repeats within TTL: retain the
baseline and consider a different strategy using its own evidence.
