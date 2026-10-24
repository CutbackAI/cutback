# Architecture

The independent package separates scanner/inventory, instrumentation/metadata, collector/PostgreSQL, pricing/valuations, datasets/evaluators/experiments and recommendations/change proposals. CLI and React use the same scoped API. Core import performs no network call or SDK instrumentation.

Scanning runs next to source; observation runs where the application executes. New metadata goes to the configured customer collector. A laptop cannot magically observe remote production. PostgreSQL is canonical; SQLite is not substituted for shared telemetry.

Telemetry keys are environment/project write-only. Control roles, separate bootstrap admin, hashed expiring/revocable keys, schema/body/time bounds and shared quotas protect distinct boundaries. Queries/mutations enforce credential scope in addition to payload project IDs.

Content is a denied-by-default separate plane. Approved examples and prompt/rubric artifacts are encrypted in customer file/S3 storage; PostgreSQL keeps safe pointers/hashes/provenance. Policy rights/vendor/environment/workflow scope, expiry, quotas and redaction precede capture/import. Provider credentials never enter events or browser responses.

Paired evaluations use a durable pre-call ledger. Jobs use row locks/leases; uncertain operations fail rather than replaying potential charges. Hash approval authorizes one run. Evidence tiers distinguish contracts, references, judges and independent labels. Changes include rollback and never deploy themselves.

Compose provides collector/worker/PostgreSQL, generated secrets and persistent volumes. A separate TLS reference adds pinned Caddy/private networking/exact proxy trust. Customer DB/secrets/identity remain supported. The free core has no hosted-account gate or company endpoint.

Dashboard OpenAPI/types are generated. Credentials stay in memory; URL filters contain no tokens, and empty states contain no fabricated telemetry/savings.
