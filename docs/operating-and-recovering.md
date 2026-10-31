# Operate and recover a self-hosted Cutback

The worker's [authenticated encrypted-object expiry](encrypted-retention.md)
handles expired orphan ciphertext independently from database maintenance and
inference. Review its file/S3 boundaries, required permissions and backup limits
before relying on physical retention in an authorized environment.

The collector, PostgreSQL, encrypted object store and encryption keys form one recovery unit. A database-only restore cannot recover deleted ciphertext or a lost key. These instructions cover the local file backend; remote S3 versioning, customer KMS, PostgreSQL PITR and clean-host disaster recovery need a drill in the actual authorized environment.

## Installation and release controls

Release work uses uv 0.13.6 and reviewed CPython 3.11.18 or 3.13.18. uv versions before 0.13.4 are affected by [GHSA-hq85-5cpj-mr62](https://github.com/astral-sh/uv/security/advisories/GHSA-hq85-5cpj-mr62). Upgrade through a trusted OS/package-manager channel before downloading dependencies; do not use the vulnerable installer to bootstrap its replacement. `scripts/check_toolchain.py` rejects vulnerable installer versions and unintended release interpreter patches. Consumer scanner compatibility remains Python 3.11–3.13; keep your application interpreter security patches current.

The Docker image installs the reviewed managed interpreter into `/opt/python` from a digest-pinned uv image on a digest-pinned Debian base. The non-root service reads that interpreter; runtime startup performs no installation. The training advisory check maps the pinned `torch +cpu` build to its upstream release version and fails on unresolved audit entries; artifact installation still verifies the original CPU hashes. This is an upstream advisory lookup, not a separate binary assessment. CodeQL acceptance inspects the generated SARIF and rejects findings or missing/failed analysis; a successful upload alone cannot authorize publication. CI and security run against the exact publish revision before distributions are built. PyPI publication uses OIDC and a named environment, never a repository API token.

## Queue and maintenance

Migrations 0005 and 0006 add durable queue eligibility and archive purpose/source dimensions; released migrations 0001–0004 are unchanged. Missing provider configuration blocks a job for 30 seconds and does not starve eligible jobs. After configuring the worker, an editor can select **Recheck configuration**. Only blocked runs with zero started requests qualify. Historical or unversioned evaluator results remain as originally recorded, but cannot authorize new acceptance, patches or rollouts under the corrected rules. Create a new comparison and approval. Expired running leases remain failed: a lost response can still be billable, so the worker never automatically replays it.

Maintenance, expiration, schedules and jobs have separate failure domains. Failed maintenance retries after 60 seconds without preventing execution of an otherwise eligible job. The SQL path extracts each event into a typed PostgreSQL record once per scan, avoiding repeated JSON decoding in every aggregate. Retention aggregates each complete UTC day in a repeatable-read transaction, then deletes precisely that snapshot's raw events. It processes at most 100 project-days per cycle and 10,000 dimension combinations per day. High-cardinality days or late events in an already archived day require operator reconciliation; they are not silently dropped. Archive dimensions retain application/evaluation/judge/unknown purpose and call-site identity. Old archives remain unknown for those dimensions. Quantiles across merged summaries remain unknown.

## Back up and verify

1. Stop collector and worker writers. Take a PostgreSQL dump and a consistent copy or snapshot of the encrypted object directory. Save the migration revision and object counts. Preserve the independent backup-encryption key and every payload decryption key in your approved secret manager, separately from backup media.
2. Use `python scripts/backup_restore.py --drill` for the owned local Compose database drill. This authenticates the complete encrypted dump before restoring SQL to a disposable database. It does not validate remote PITR.
3. Restore the database and encrypted objects into an isolated destination, with the correct keys and execution disabled. Verify every live `PayloadSample.object_ref`, cache entry and training artifact pointer, scope, hash and expiry before accepting the restore. Do not extend expired content to make the drill pass.
4. Run tenant/RBAC tests, metadata reports and authorized sample reads, then a synthetic queue job. Record the observed recovery duration and cutoff. Only an operator's reviewed recovery decision should redirect application traffic.

`test_object_recovery.py` exercises real database pointers against restored ciphertext, old/new keys, repeated local rekey, unchanged authenticated expiry, wrong-tenant denial and tamper rejection. It complements the database dump drill; it is not evidence of a successful customer disaster recovery.

## Rotate payload keys without extending retention

Set `CUTBACK_PAYLOAD_KEY` to the new base64-encoded 32-byte key. Supply previous keys, at most three, through `CUTBACK_PAYLOAD_PREVIOUS_KEYS_FILE`, an external secret file containing a JSON array of base64 strings. Protect this file using the operating system's account/ACL controls. Never commit it, print its contents or include it in an export. Readers try authenticated decryption with the active and previous keys; all new writes use the active key. Existing envelope formats stay readable.

For the local file backend, stop writers and verify the backup first. A maintenance program using `configured_store()` may call `store.rekey(project_uuid, object_ref)` for each validated, unexpired database reference. This authenticates the old envelope, atomically replaces its ciphertext with a fresh nonce, and preserves its reference and authenticated expiry. Repeating an interrupted rekey is safe. Validate the resulting objects with a store configured with the new key only before retiring old keys. Do not remove old keys until all retained objects, backups and retention obligations have been reviewed. For S3, preserve previous keys until a separately reviewed version-aware object migration and recovery drill succeeds; this release does not claim a tested live S3 rotation.

## Staging acceptance still needs your environment

Before real inference, record the permitted application/data, provider/model versions, maximum requests and dollars, deployment destination, quality criteria, independent labels, rollback owner and retention period. Default budgets remain zero and execution disabled until authorized. Real provider behavior, serverless freeze/drain, invoice reconciliation, IdP/S3/cluster recovery and business-quality outcomes cannot be established by synthetic fixtures.
