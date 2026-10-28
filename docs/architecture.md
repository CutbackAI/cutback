# Architecture

The optional [prompt registry](prompt-releases.md) stores encrypted bodies/schemas plus relational immutable version/dependency/alias/grant/use metadata. Scoped resolution renders variables in the application; telemetry ingestion never grants content reads. Playground compilation produces frozen native experiment contracts under the existing reservation worker. Source-bound [local application checks](application-checks.md) run only through an explicit development/CI command, outside the scanner and shared server. Their receipts have declared source and attestation limits.

The independent package separates scanner/inventory, instrumentation/metadata, collector/PostgreSQL, pricing/valuations, datasets/evaluators/experiments and recommendations/change proposals. CLI and React use the same scoped API. Core import performs no network call or SDK instrumentation.

Scanning runs next to source; observation runs where the application executes. New metadata goes to the configured customer collector. A laptop cannot magically observe remote production. PostgreSQL is canonical; SQLite is not substituted for shared telemetry.

Telemetry keys are environment/project write-only. Control roles, separate bootstrap admin, hashed expiring/revocable keys, schema/body/time bounds and shared quotas protect distinct boundaries. Queries/mutations enforce credential scope in addition to payload project IDs.

Content is a denied-by-default separate plane. Approved examples and prompt/rubric artifacts are encrypted in customer file/S3 storage; PostgreSQL keeps safe pointers/hashes/provenance. Policy rights/vendor/environment/workflow scope, expiry, quotas and redaction precede capture/import. Provider credentials never enter events or browser responses.

Qualified import adapters project bounded native JSONL, pinned OpenInference OTLP JSON and Promptfoo result exports without executing the source's config, providers or graders. Offline preview returns metadata only; encrypted preview/commit uses explicit rights and an exact source hash. Historical outputs and externally asserted scores do not become independent gold.

Frozen review queues authorize individual current scoped-key/OIDC principals. Encrypted rubric/source/judgment objects are separate from safe queue/item/lease/vote/resolution records. Reviewer endpoints enforce blind answers; designated adjudicators see completed disagreements, and promotion creates a new frozen dataset with exact receipts. Source and destination rights remain necessary for content access; safe status metadata and closure remain available after content expiry.

Persistent per-project input/customer-group families and pre-call exposure receipts prevent reimports from resetting a consumed holdout. Transitive family discoveries never rewrite old content or split assignments. The dataset hash binds those frozen assignments and provenance. Current lineage is rechecked before accepting staging/patch/rollout evidence. Legacy identities require authorized conservative reconciliation; unknown history cannot be silently declared fresh. Additive migrations 0007–0009 introduce workflow traces, dataset lineage and review records while preserving released earlier migrations.

Paired evaluations use a durable pre-call ledger. Jobs use row locks/leases; uncertain operations fail rather than replaying potential charges. Hash approval authorizes one run. Evidence tiers distinguish contracts, references, judges and independent labels. Changes include rollback and never deploy themselves.

Compose provides collector/worker/PostgreSQL, generated secrets and persistent volumes. A separate TLS reference adds pinned Caddy/private networking/exact proxy trust. Customer DB/secrets/identity remain supported. The free core has no hosted-account gate or company endpoint.

Dashboard OpenAPI/types are generated. Credentials stay in memory; URL filters contain no tokens, and empty states contain no fabricated telemetry/savings.

Optional modules preserve the independent core. Organization membership and project grants constrain OIDC subjects; PKCE state is single-use and browser tokens stay in memory. Billing is a configured Stripe connector behind explicit flags and does not gate self-hosted OSS. Scoped cache entries use the same authorized encrypted store and shared capture quotas.

Routing reserves the worst allowed target and makes exactly one selected call, with no paid classifier or fallback. Validation pilots never consume final test groups. Local training runs in a separate frozen HF/PEFT process with no provider/admin credentials, bounded termination grace, durable one-shot reservations and encrypted checksummed adapters. Training losses are not quality evidence.

Revision 0003 adds optional operational tables; 0004 scopes project names to organizations. Released migrations stay immutable. Maintenance respects per-project retention. Helm separates migration, collector, inference worker and optional training credentials/resources; Terraform targets an already approved cluster and namespace.

The agent workflow adds a local atomic, locked metadata journal beside the application; it does not move source analysis into a hosted agent or require a corporate endpoint. Source/config/prompt dependency and native request hashes bind the baseline, frozen plan, worker evidence and proposed diff. Actual request templates/options/instructions remain encrypted under the selected policy. Metadata checkpoints never contain provider keys, request content or examples. Collector plan/run records remain canonical for authorization and billing; a journal phase cannot grant either.

The installed wheel can supervise a loopback collector with one labelled Docker PostgreSQL container and persistent volume. Secret-bearing configuration stays in the application's ignored private workspace. This process is separate from the invoking CLI; stopping preserves the database/encrypted store. A local worker invocation selects only the saved approved run. Remote Compose/worker/operator recovery paths remain supported.
