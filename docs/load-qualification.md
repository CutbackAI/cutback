# Qualify an owned local ingest/query envelope

Locally accepted source-only operator functionality; hosted/publication gates remain in [progress](progress.md). PyPI dev5 does not contain it. The v1 CLI below measures actual local HTTP/PostgreSQL paths using independent synthetic metadata. Its ingest/query phases do not qualify model performance, customer traffic or worker/gateway capacity. A separate simultaneous service harness is described below; neither establishes a production SLA.

Use an isolated source-checkout `server` environment and an owned local collector. Metadata storage is sufficient; raw content capture and provider credentials are unnecessary. This command creates two synthetic projects and expiring ingest/viewer credentials, then deletes only the projects whose creation IDs it recorded. It leaves other projects alone. Review its resource limits before explicitly running it.

```sh
cutback collector up --target . --port 8080
cutback qualify load --target . \
  --profile examples/load-qualification/profile.json --execute-local
```

The example profile declares 10,000 total unique events, batches of 100, concurrency four, forty trace and usage queries, a five-minute workload ceiling and minimum twenty raw samples for each latency gate. Its illustrative ceilings are p95 ingest/replay/span requests below ten seconds, p95 queries below five seconds and at least ten unique events per mixed-ingest-workload second. These are reviewed **local test thresholds**, not advertised product performance. Choose and freeze the intended hardware/topology and a suitable profile before qualification.

Two explicit phases run: concurrent native-event metadata ingestion/duplicate replay/root-span ingestion, then concurrent trace and usage queries over retained data. Queries are not measured simultaneously with ingestion. The throughput denominator includes the replay/span workload; it is not a pure ingest endpoint benchmark. Client request timings include transport/server/response reading but exclude waiting for the client semaphore. Peak client in-flight count is recorded; server queueing is reflected in request wall time. Neither value establishes actual server CPU concurrency or production saturation behavior.

Each tenant receives a separate namespaced synthetic dataset. Half the event models are deliberately unpriced. The runner checks exact acknowledgement and aggregate counts, duplicate idempotency, catalog-derived known subtotals, preservation of unknown full cost, scoped trace rows and viewer cross-tenant denials. It never forwards these event declarations to a model provider. A successful execution proves these bounded checks for this run, not complete multi-tenant isolation under every workload.

The private receipt is saved under `.cutback/qualification/`. It contains the exact profile/hash, immutable catalog hash, OS/architecture/Python, acknowledgements, raw bounded nanosecond latency samples, nearest-rank descriptive p50/p95, gates, sanitized errors and cleanup receipts. Quantiles stay unknown below the declared sample minimum. It retains no keys, HTTP response/error bodies, prompts, application source or customer data. The receipt's hash detects alteration relative to that receipt; it is not independent authenticity attestation.

The command returns nonzero if the declared gates fail or execution/accounting/cleanup is incomplete. Timeouts, malformed or oversized responses, redirects, transport errors and an unmet SLO cannot become a pass. Outstanding client tasks are cancelled and joined before cleanup. A lost project-create acknowledgement remains an unresolved exact generated slug; the runner does not infer an ID or delete a merely similar project. Inspect/reconcile that synthetic project under the operator's own authority. Failed cleanup is visible and must be completed before treating the environment as clean.

Deletion is followed by a scoped project-absence check before cleanup is confirmed. A hard process/host kill can bypass cleanup and leave synthetic projects; reconcile them under the operator's own authority before restarting a trial. A currently interrupted trial is not automatically replayed or declared clean. Failed/cancelled client attempts remain in latency samples and errors block the overall pass.

Limits: numeric loopback HTTP destinations only, no redirects or inherited HTTP proxy configuration; two tenants; 200–50,000 total events divided equally into whole batches of 50–200; concurrency 1–16; 4–200 query pairs; workload 10–600 seconds; 2 MiB per response; maximum 10,000 raw samples per metric. Existing server quotas remain enforced. A profile exceeding them fails; the runner does not widen quotas. Setup and cleanup have separately bounded HTTP calls and are excluded from measured workload throughput.

For repeatable comparison, retain the profile and both receipts, use the same topology/dependency versions/data size, separate cold/warm trials explicitly and keep failures in the denominator. Hardware details, storage provisioning and competing load are operator-controlled context; this v1 receipt does not discover or attest them. Sustained mixed ingest/query/online-evaluation/worker tests, recovery/load shedding and intended customer/cloud topology remain separate Release 7 qualification work.

The [paired example](../examples/load-qualification/compare.py) runs before → after → after → before against two separately started owned local collectors. It saves completed trial receipts privately after each trial, stops on unconfirmed cleanup and retains incomplete comparisons. Summary quantiles are recomputed from the combined raw samples, never averaged from p95 values. Code identity/hardware are operator-controlled; cache/background load is not inferred. A local diagnostic can reveal a regression without establishing its cause.

The source ingestion implementation now uses bounded set operations: one correlated effective-rate lookup and one stable-order multi-row event upsert; scoped bulk trace/session/span identities with fingerprint conflict rollback and one durable intake record per newly accepted span; and one grouped count query for a retained trace page. First observation/duplicate semantics, Decimal pricing, effective windows/region/tier scope, atomic billing and existing quotas stay enforced. Additive migration `0016` indexes project/provider/model/effective date; released `0001`–`0015` are preserved. Run upgrades under the installation's reviewed migration/maintenance procedure; no production migration is performed by this example.

## Simultaneous synthetic service qualification

The source-only development harness **passed local component qualification**;
exact public/hosted acceptance remains pending. [Executed evidence and raw
durations](evidence/mixed-service-capacity-acceptance.json) record the two complete
ten-minute SDK runs and fault checks. It creates
one disposable local PostgreSQL database, migrates it using the actual released
migrations, starts two owned loopback servers and creates two synthetic tenants.
Five actors per tenant run during the same declared window: native metadata
ingestion with duplicate replay and root spans, trace/usage queries, approved
experiment preparation/queue/execution, encrypted-content intake and online
model judging, and an actual native SDK client through the gateway. Provider
transports are closed HTTPX fixtures. No provider account or paid call is used.
This harness requires source test/development dependencies; it is not part of
the minimal offline scanner and is absent from PyPI dev5.

Review [mixed-profile.json](../examples/load-qualification/mixed-profile.json)
before execution. It declares 600 seconds, two tenants, ten actors, batches of
ten metadata events, a two-second pause between each actor's completed
operations, and at least twenty **within-window completions per actor**. Maximum
5,000 operations per actor and 10,000 raw samples per kind are hard bounds.
Reaching the operation cap before the window ends fails instead of pretending
the test was sustained. A 180-second drain may finish known work; its completions
do not satisfy within-window sample requirements or throughput. The illustrative
p95 ceilings are sixty seconds per whole HTTP/quality actor operation and 180
seconds per whole experiment batch. These broad local correctness thresholds
are not advertised application latency targets.

From a reviewed source checkout with its locked development dependencies and
the owned Compose PostgreSQL server running, pass its local disposable-database
connection through `CUTBACK_TEST_DATABASE_URL` in the process environment:

```sh
uv sync --locked --all-extras --dev
uv run python scripts/mixed_capacity_acceptance.py \
  --profile examples/load-qualification/mixed-profile.json \
  --receipt .cutback/qualification/mixed-capacity.json
```

The database credential must permit creation and removal of a new disposable
database. The runner rejects a remote host and never migrates the supplied base
database. It creates and later force-removes only its own exact random database.
Before creation it writes an adjacent private `.intent.json` with that random
database identity and profile hash, never credentials. Keep it with the final
receipt. A missing final receipt or an unconfirmed create reply requires
operator reconciliation; reusing the same receipt path is refused automatically.
Missing receipt directories are created through the protected-output helper;
symlink or redirected parents are refused before database writes.
Each tenant is also deleted through the actual API and checked absent. A lost
project-create reply is reconciled only against that exact generated slug. A
separate supervisor bounds migration and the complete test process tree; a
worker that does not drain cannot hang this qualification indefinitely. Timeout,
partial accounting, missing/stale/tampered receipt, incomplete cleanup, or failed
latency/sample gates returns nonzero. The supervisor records failure rather than
retrying billable work. Abrupt loss of the supervisor/host itself can bypass its
cleanup; operator reconciliation remains necessary after that event.

Receipts retain bounded raw operation intervals and recompute descriptive
nearest-rank quantiles from them. Durations include each actor's full work, such
as multiple ingestion HTTP requests or queueing and completing one experiment;
they are not per-endpoint CPU timings. Client concurrency measures overlapping
actor operations, not simultaneous server CPU execution. Reported rates count
completed operations in the window; metadata throughput multiplies those
completed cycles by their batch size. Setup, drain and cleanup are excluded.
CPU count, architecture, Python and native SDK versions are recorded, while
database resources and competing load remain operator-controlled and unattested.

The runner reconciles exact unique metadata and duplicate acknowledgements,
half-known/half-unknown catalog prices and Decimal known subtotals. Unknown full
cost remains null. Actual worker request/spend totals match durable per-call
rows; online judge totals match settled evaluations and rules; gateway attempts
match settled receipts. Cumulative reserved upper bounds are not confused with
unsettled calls or actual spend. Unknown charges fail the success envelope.
Cross-tenant viewer requests must be denied. Synthetic judge outcomes and
catalog-modeled usage costs remain synthetic; customer quality, invoice cost,
production reliability, live provider latency and deployment-scale capacity
remain unverified. [Progress](progress.md) records executed gates and failures.
