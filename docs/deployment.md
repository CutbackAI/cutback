# Self-hosted deployment

## Local reference

```sh
uv sync --locked --all-extras --dev
uv run python infra/compose/prepare.py
docker compose -f infra/compose/docker-compose.yml up -d --build
uv run python scripts/demo.py
uv run python scripts/backup_restore.py --drill
```

Collector: loopback 8080; PostgreSQL: loopback 55432. Images/lockfiles are pinned, ignored secrets are generated, collector/worker run nonroot on read-only filesystems, and volumes persist. Maintenance runs with inference disabled. Stopping preserves data; removing volumes is a separate destructive decision.

The backup drill encrypts a development pg_dump, authenticates before restoring into a separate disposable database, and compares table row counts. It never replaces the application DB. Normal operations use the script's backup/restore functions with a private key and operator-selected commands. Keep keys separately, protect temporary-directory ACLs, and own scheduling, recovery objectives, backup expiry and legal holds.

## Administration

Load secrets privately or through a customer secret manager. Never paste values into chat, source, argv or shared logs. `CUTBACK_ADMIN_TOKEN` is installation bootstrap; `CUTBACK_CONTROL_TOKEN` is a scoped control key. Applications receive only their project/environment ingestion key.

```sh
cutback projects create my-project
cutback keys create --project PROJECT_UUID --role ingest --output .cutback/ingest-key.json
cutback keys create --project PROJECT_UUID --role viewer --output .cutback/viewer-key.json
cutback report --source collector --project PROJECT_UUID --period 1h
cutback opportunities --source collector --project PROJECT_UUID --period 7d
```

Key files use exclusive creation and are never printed. Restrict Windows ACLs. Server hashes, expiry and revocation apply. Rotate through replacement, runtime-secret update, verified ingress, then revocation. Clear bootstrap variables afterward. Viewer/analyst/editor/project_admin have distinct scoped capabilities; enterprise OIDC is later scope.

## PostgreSQL and HTTPS

Supply `CUTBACK_DATABASE_URL=postgresql+psycopg://...` through runtime secrets. Use verified PostgreSQL TLS/CA, private networking and separate migration/runtime privileges for an external DB. SQLite is rejected. `cutback db migrate` upgrades immutable revisions under an advisory lock. Back up before upgrade; pre-alpha versions have no production compatibility guarantee. Downgrade is not a restore plan.

`docker-compose.tls.yml` is a separate reference, not a dev overlay. Set approved `CUTBACK_DOMAIN`/`CUTBACK_TLS_EMAIL`; start through an authorized release process. It publishes proxy 80/443, keeps PostgreSQL internal and trusts exactly the proxy's private IP for forwarded HTTPS. Change its dedicated subnet and trust address together if needed. Certificates are obtained only when deliberately started. Config validation is not a deployed TLS test; no public host, DNS or certificate was provisioned.

The SDK requires HTTPS outside exact loopback names, rejects endpoint credentials/query strings, and never follows redirects. Never use wildcard proxy trust or expose a plaintext backend publicly. Customer network/identity/secrets still need staging acceptance.

## Retention and deletion

Upgrade window for this development line: dev1 retains dev0's metadata event schema and adds revision 0002 after immutable 0001. Back up first, upgrade collector/worker together, then SDKs. Existing metadata and scoped keys are retained; new operational APIs require dev1. Keep old binaries and the encrypted pre-upgrade backup for rollback through restore into a separate database. Older or future development versions need their own reviewed migration/compatibility evidence. This limited development window is not a production support commitment.

The worker archives complete days, prunes raw events after 30 days and daily summaries after 395 days, and deletes registered sample/plan artifacts at expiry (7 days default). `cutback worker maintain` previews; `--apply` executes. Shared locking/audit apply. A day above the bounded aggregation limit is refused before deletion; partition before exceeding that envelope. Late events for archived days stop pruning for reconciliation rather than overwriting evidence.

Reports flag partial retained days and unknown combined quantiles/logical identities. Shared authenticated quotas complement an operator edge gateway/WAF for anonymous floods. SDK counters are cumulative per process instance, not population coverage. `/metrics` requires installation admin scope.

Project export streams administrative NDJSON without raw samples or credentials. Project deletion requires admin and exact `confirm_slug`, refuses queued/running jobs, deletes encrypted objects/scoped records, and retains a minimal receipt. Cancel/finish jobs first. Backup/version history, legal holds, orphans and storage/DB failure reconciliation remain operator lifecycle responsibilities; one API call is not a compliance guarantee.

## Optional content and inference

Keep `CUTBACK_ALLOW_PAYLOADS=false` until approved. Configure an independent base64 32-byte `CUTBACK_PAYLOAD_KEY` through a secret store. File mode uses `CUTBACK_PAYLOAD_DIR` on a protected persistent volume. S3 uses `[storage]`, `CUTBACK_PAYLOAD_BACKEND=s3`, `CUTBACK_S3_BUCKET`, optional customer endpoint and region settings. Credentials come from customer workload identity/environment; there is no founder bucket/account. Configure private ACLs, version/lifecycle/quota monitoring, key recovery/rotation and orphan reconciliation.

Collector and worker need the same store/key. Policies separately scope rights, vendors, workflows, environments, sampling, bytes and expiry. Revocation stops future access; an authorized in-flight call may finish. Maintenance can delete expired registered artifacts even while new capture is disabled.

Inference stays disabled until exact budget approval. Configure `CUTBACK_EXPERIMENT_EXECUTION=true` and distinct approved worker credentials/local endpoint; install its required provider extra. Never give it application production tool credentials. See evaluation.md for reservations, crash behavior and quality scope.
