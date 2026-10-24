# Contributing

Use Python 3.11+, uv, and a local PostgreSQL 17 server for integration tests.

```sh
uv sync --locked --all-extras --dev
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest tests/unit
uv run python scripts/check.py
uv build
uv run python scripts/wheel_smoke.py
```

All provider test data must be synthetic or rights-cleared and labeled. Never use live inference in ordinary CI. A live test requires specific vendor, data and spend approval. Keep schema changes in new Alembic revisions; add migration and privacy tests. New adapters require pinned versions and sync/async/stream/error evidence before claiming support. Keep unsupported behavior in docs/providers.md and progress.md.

Contributions are submitted under Apache-2.0. Retain third-party notices. Report vulnerabilities privately according to SECURITY.md. Hosted CI validates Python 3.11 and 3.13 with PostgreSQL and synthetic provider responses; see the repository Actions page.

