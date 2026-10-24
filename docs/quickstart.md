# Quickstart

Python 3.11+ is required. Scanning needs no account, key, cloud, Docker or paid request.

```sh
python -m pip install --pre cutback
cutback doctor --json
cutback scan /path/to/app --json --output .cutback/scan.json
cutback opportunities --target /path/to/app
cutback instrument --target /path/to/app --entrypoint main.py --plan --json
```

The final command generates a diff; apply/deploy are separate explicit actions. Inspect startup order, supported SDK pins/wrappers, collector scope and bounded Lambda flushing. `.env` secrets are not parsed; nested ignores/config exclusions apply. `cutback init --mode local` writes safe defaults without overwriting.

```sh
uv sync --locked --all-extras --dev
uv run python infra/compose/prepare.py
docker compose -f infra/compose/docker-compose.yml up -d --build
uv run python scripts/demo.py
uv run python scripts/check.py -q
```

The demo uses an actual SDK/HTTP collector/PostgreSQL and synthetic inference. No paid call occurs. Tests create unique disposable loopback DBs; absent DB configuration causes explicit skips, not SQLite substitution.

Open `http://127.0.0.1:8080/dashboard/` with a separate control key. Load the local bootstrap secret privately from its ignored generated file, never print it. Issue scoped keys for normal investigation.

For browser development run npm ci in web, export OpenAPI from the root, then npm run types/build in web. Install Chromium and set `CUTBACK_TEST_BROWSER=true` for PostgreSQL tests. They exercise actual APIs and disposable synthetic projects.

See deployment.md, providers.md, evaluation.md and agent-integration.md for configuration and acceptance limits.
