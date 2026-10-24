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

## Guided workflow

Connect using the local bootstrap credential privately, then issue scoped keys for normal use. Create a project, upload its offline scan, and observe synthetic examples first. The Security page creates project keys, retention rules and optional capture policies. Datasets accepts bounded CSV/JSON/JSONL after explicit rights confirmation. Experiments drafts and freezes a plan, shows the complete budget, and requires the exact plan hash approval before queueing. Cancel stops further calls; uncertain in-flight charges retain reservations.

No dataset is required for inventory and metadata reports. Missing independent labels remain provisional evidence. Model output is never silently treated as gold.

See [runnable examples](../examples/README.md) and run `uv run python examples/experiment_walkthrough.py` for a complete synthetic experiment using real HTTP and PostgreSQL. Use `uv run python scripts/scale_smoke.py` for a bounded local ingestion/replay smoke; neither script calls paid providers.

Optional JS scanning: install `cutback[javascript]` and repeat `--language python --language javascript --language typescript`. Native Node instrumentation lives in `packages/js-sdk` with pinned compatibility tests; it is not an npm publication claim.
