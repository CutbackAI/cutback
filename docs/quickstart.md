# Quickstart

Requires Python 3.11+ and uv. Docker is only needed for local collector/PostgreSQL tests. No provider key is needed below.

```sh
uv sync --locked --all-extras --dev
uv run cutback --version
uv run cutback doctor --json
uv run cutback scan examples/minimal-openai --format table
uv run cutback scan examples/fastapi-mixed-providers --format json --output .cutback/scan.json
uv run cutback report --source scan --target examples/fastapi-mixed-providers
uv run cutback opportunities --target examples/fastapi-mixed-providers
uv run cutback instrument --target examples/minimal-openai --entrypoint main.py --plan --json
```

The last command produces an exact bootstrap diff. It writes nothing unless `--apply` is explicitly passed. Review process entrypoints, dependency pins, existing observers, environment settings and Lambda flushing before applying any integration patch. No deployment command is part of applying a local source diff.

`cutback init --mode local` writes `.cutback/config.yaml` with scanning enabled, telemetry/content capture disabled and paid calls forbidden. It refuses to overwrite an existing file. The parser rejects unknown configuration keys. Root `.gitignore` and configured exclusions are honored; nested gitignore inheritance is not yet implemented. Secret `.env` files are never parsed by the scanner.

For PostgreSQL-backed acceptance tests:

```sh
uv run python infra/compose/prepare.py
docker compose -f infra/compose/docker-compose.yml up -d --build
uv run python scripts/check.py -q
```

`scripts/check.py` loads a local secret file without printing it. Tests create a uniquely named disposable database on the loopback PostgreSQL server; they do not reset the application database. With a different local test server, set `CUTBACK_TEST_DATABASE_URL` securely. `pytest` without that variable skips database tests and explicitly reports the skips. It does not replace PostgreSQL with SQLite.

The end-to-end fixture runs the real pinned SDK against synthetic HTTP responses. It does not send a paid inference request and does not establish live-provider acceptance. Example application sources are for scanning and are not automatically run.

