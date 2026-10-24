# Cutback

Cut your AI costs. Keep your quality.

Cutback discovers AI calls, observes supported runtime usage, evaluates authorized alternatives, and prepares reviewable changes. Apache-2.0 includes the CLI, collector, PostgreSQL, worker, encrypted datasets and React dashboard. No corporate account or hosted service is required.

**Pre-alpha, not production validated.** Tests use synthetic provider responses. Real pilot traffic, representative quality validation, paid requests and production deployment remain explicit approval gates. See [progress](docs/progress.md) and [limitations](docs/limitations.md).

```sh
python -m pip install --pre cutback
cutback doctor --json
cutback scan /path/to/python/project --json
cutback opportunities --target /path/to/python/project
```

Scanning is offline and never executes the application. Historical tokens, spend, latency and quality remain unknown from source alone. Lower prices establish a candidate, not preserved quality or realized savings.

```sh
uv sync --locked --all-extras --dev
uv run python infra/compose/prepare.py
docker compose -f infra/compose/docker-compose.yml up -d --build
uv run python scripts/demo.py
uv run cutback dashboard --local
```

The demo uses synthetic inference and an actual local collector/database. Dashboard credentials are separate control keys held only in browser memory. Read [deployment](docs/deployment.md), [evaluation](docs/evaluation.md), [provider compatibility](docs/providers.md), and [agent setup](docs/agent-integration.md).

Extras: `cutback[openai]`, `[anthropic]`, `[instrumentation]`, `[server]`, `[eval]`, `[storage]`. The scanner core contains no SDK, server or GPU dependencies. No calls home, implicit paid inference, automatic production patches or traffic changes.
