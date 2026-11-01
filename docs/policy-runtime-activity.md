# Inspect what a source policy actually does

This source feature passed local component qualification and awaits public hosted
acceptance. It is absent from PyPI `0.1.0.dev5`.
Use [the progress record](progress.md) for installed-wheel, container and hosted
acceptance. The qualified v1 improvement program continues to refuse a cached
current reference. The separate [v2 path](source-policy-cached-current.md) is
under qualification and uses these observations without treating them as
inference approval, population coverage or production savings.

A policy can declare exact caching while its application runs without the private
cache scope file. Provider-call telemetry also misses cache hits because a hit
does not invoke the provider. Policy activity records the logical operation
separately from native provider observations, so these cases remain visible.

## Enable metadata observation

Initialize once in the process entry point, outside the fixed policy consumer.
Use an existing environment-scoped ingest key and the current scanner source map:

```python
import cutback

observer = cutback.init(
    service_name="independent-support",
    environment="staging",
    enabled=True,
    endpoint="http://127.0.0.1:8080/v1/ingest/events",
    project_id="YOUR_PROJECT_UUID",
    source_map=".cutback/source-map.json",
    observe_policy_runtime=True,
    capture_content=False,
)
```

The scoped key comes from `CUTBACK_PROJECT_TOKEN`; do not put it in application
source. Generate or refresh the source map from clean committed source using
`cutback agent refresh-source-map --target .`. Restart observation after a source change.
No private runtime cache file is read by the scanner or the inspection command.
The existing runtime cache adapter reads it only when the application explicitly
supplies that file for a cached policy call.

Run the application's normal workload through its supplied `responses_create`
or uncached `responses_create_async` wrapper. This observer sends no application
input, instruction, response, exception text, cache request fingerprint, private
file path, or credential. It copies a typed receipt from the cache adapter;
it does not inspect the cached response. It requires a matching, non-stale source
map call site and commit. The workflow comes from the validated policy
configuration and may differ from the scanner's entry-point workflow identifier.
These are client-reported identities, not independent deployment attestation.

Observation is explicit and defaults off. Its bounded memory queue exports in a
background thread with no retries, redirects, proxy-environment inheritance or
disk spool. Queue overflow, collector errors and shutdown can lose metadata;
the SDK response or exception still propagates. `observer.health()` exposes
`policy_runtime_*` counters, also submitted to the collector's scoped health
endpoint. Call `observer.flush()` / `observer.close()` at the appropriate process
lifecycle boundary; neither turns best-effort delivery into complete coverage.

## Inspect the current source automatically

```sh
cutback source-policy inspect-activity --target . --producer app.py \
  --service independent-support --environment staging --window-minutes 60 \
  --endpoint http://127.0.0.1:8080 --project YOUR_PROJECT_UUID
```

Use the existing private control connection or `CUTBACK_CONTROL_TOKEN` with a
viewer-capable scoped credential. This command qualifies the complete fixed
consumer and policy from clean Git source without importing the application or
provider SDK. It derives the call site, commit, policy hash and workflow, requests
only their retained metadata, and checks the source again before returning.
It sends no inference, grants no execution approval and changes no source.
`--service` is optional when exactly one matching retained service exists. The
bounded discovery query never selects an ambiguous or truncated service list by
recency. An explicit name remains available when several services share a source.

`POST /v1/projects/{project_id}/policy-runtime/inspect` accepts the same explicit
scope plus an aware start/end window of at most 30 days. It requires project read
permission. `POST /v1/ingest/policy-runtime` requires a current ingest credential
for the exact project and environment; shared credential quotas and accepted-event
metering apply. Batches contain 1–200 events, with the shared 1 MiB request bound
and a 30-day past / five-minute future ingestion window. Identical event IDs are
idempotent within a project/environment; changed data for an existing ID rolls
back the whole batch with a conflict.

## Read the evidence correctly

| Observed operation | Native SDK invocations | Cache operations | Meaning |
|---|---:|---:|---|
| Cache configured, private scope absent | 1 | 0 | Configuration alone did not request caching |
| Exact encrypted cache hit | 0 | 1 | Historical response usage is not new provider usage |
| Miss with attempted write | 1 | 2 | Lookup and attempted write, including uncertain acknowledgements |
| Miss whose response is ineligible for storage | 1 | 1 | No cache write was attempted |
| Requested cache operation fails or is cancelled | Unknown | Unknown | Do not infer that no provider call or charge occurred |

An SDK invocation can contain the application's native retry attempts. These
counts are not wire-attempt counts, invoice costs, task success or savings.
The optional cache cost is the policy owner's declared per-operation **upper
bound**, multiplied using exact Decimal arithmetic. An uncertain write still
counts its attempted operation. A failed active-cache operation preserves unknown
counts and costs; it is never retried automatically by this observer.

Inspection covers at most 10,000 retained operations, ordered by start time and
event identity. It includes a digest of their immutable identities/fingerprints,
the exact project/environment/source scope, state and completion counts, and
known counts alongside unknown counts. Empty, truncated or partly unknown
evidence cannot become a complete invocation/cost total. Capture coverage,
invoice-verified cost and business-task success remain unknown. This digest is
an inspection receipt, not a frozen experiment authorization or tamper-proof
external attestation. Concurrent intake or retention can change the next result.

Raw metadata follows project retention. Its deletion leaves no aggregate that
pretends to reconstruct old cache activation, costs or a missing population.
Admin metadata export includes these rows; project deletion removes them through
the project foreign key. Migration `0035` is additive and refuses downgrade while
activity exists: export and explicitly remove it before downgrading. Released
migrations `0001`–`0034` are unchanged.

Cached async responses and streaming policy calls remain unsupported. The
separate encrypted cache and payload policies still govern content retention,
current rights, freshness and revocation. Metadata observation grants none of
those permissions.
