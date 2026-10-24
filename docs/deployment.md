# Local self-hosted deployment

The current reference profile is for local development, with collector `127.0.0.1:8080` and PostgreSQL `127.0.0.1:55432`. It creates no external resources. Image manifests and Python dependencies are pinned. Runtime secrets are generated locally and excluded from Git/build context.

```sh
uv run python infra/compose/prepare.py
docker compose -f infra/compose/docker-compose.yml up -d --build
uv run python scripts/demo.py
uv run python scripts/check.py -q
```

`demo.py` uses the pinned OpenAI SDK with a **synthetic HTTP transport**, then sends metadata to the running Compose collector. It creates a labeled synthetic project and writes a labeled report under `artifacts/`. No provider key, paid request or customer content is used. The script loads the local bootstrap token privately and creates separate write/view credentials without printing them.

For manual administration in PowerShell:

```powershell
$env:CUTBACK_ADMIN_TOKEN = (Get-Content -Raw infra/compose/secrets/admin_token).Trim()
$env:CUTBACK_CONTROL_TOKEN = $env:CUTBACK_ADMIN_TOKEN
uv run cutback collector status --endpoint http://127.0.0.1:8080
uv run cutback projects create my-local-project
# Use the returned project UUID below. Token creation writes a new file, never stdout.
uv run cutback keys create --project <UUID> --role ingest --output .cutback/ingest-key.json
uv run cutback keys create --project <UUID> --role viewer --output .cutback/viewer-key.json
$env:CUTBACK_CONTROL_TOKEN = (Get-Content -Raw .cutback/viewer-key.json | ConvertFrom-Json).token
uv run cutback report --source collector --project <UUID> --period 1h
```

`--output` credential files must have private permissions. On Windows, apply an ACL restricted to the current account. Never paste file contents into chat or shared terminal logs. Clear bootstrap admin variables when administration ends. Applications receive only the environment-scoped ingestion token, never bootstrap or viewer tokens. Keys expire (30 days default); revoke through authenticated `DELETE /v1/projects/{project}/keys/{key}`. Rotate by issuing a replacement, updating the runtime secret, verifying ingress, then revoking the old key.

The container reads secrets through mounted files, migrates PostgreSQL, and starts a nonroot read-only collector. Its `CUTBACK_DEV_HTTP=true` is deliberately limited to this loopback profile. Compose secrets are files, not an encrypted secret manager. The DB volume persists across container restarts. `docker compose ... stop` stops services and preserves data. Do not remove volumes without an approved backup/deletion decision.

## External PostgreSQL and HTTPS

The collector accepts `CUTBACK_DATABASE_URL=postgresql+psycopg://...` from the customer's runtime secret system, plus a separate high-entropy `CUTBACK_ADMIN_TOKEN`. Supply PostgreSQL TLS options (`sslmode=verify-full`, trusted CA as appropriate), network policy, backups and least-privilege runtime/migration roles. SQLite URLs are rejected. Migrations are available through `cutback db migrate`; no database URL appears in command arguments.

Before a public endpoint is enabled, finish M3 operational gates: TLS termination or direct HTTPS, private backend network, DB access isolation, shared rate quotas, retention/deletion worker, restore drill, security review and identity management. The SDK rejects non-HTTPS endpoints outside exact loopback names and refuses endpoint credentials/query strings. Redirects are not followed.

The development server disables proxy-header trust. A reverse proxy on a private network can terminate TLS and connect to a backend with an explicitly configured internal-HTTP policy, but the collector must never be exposed directly in that configuration. Trust only a known proxy and enforce HTTPS at the edge. No production TLS deployment has been tested here. Do not copy the dev Compose profile to a public host unchanged.

## Migrations, backup and recovery

Revision `0001` defines the foundation. Tests create a fresh PostgreSQL DB, upgrade, downgrade, upgrade again and check ORM drift. Future releases must add revisions, preserve the current wire version for an announced upgrade window, and document compatibility before release. There are no production upgrade guarantees yet.

Before an upgrade: stop writers or define a consistent snapshot, create an encrypted `pg_dump -Fc` using runtime DB credentials, record the schema revision/catalog/package versions, and test restoring it into a separate database using `pg_restore`. Verify project/key counts, representative event IDs and usage report totals before switching traffic. Keep backup encryption keys separately. A migration downgrade is destructive and is not a replacement for restore. Do not print DB credentials or embed them in argv.

Backups, retention scheduling and a real restore drill remain required M3 work. No production backup or cloud deployment has been performed by this implementation.

