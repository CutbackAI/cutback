# Reproducible synthetic demo

For a first introduction, run `uv run cutback demo` with Docker Desktop running and follow [the support-desk walkthrough](testing-the-sample.md). It retains an explorable workspace with completed quality comparisons, encrypted case evidence, stage receipts and source/rollback diffs. The scripts and dev2 recording below remain maintainer regression tools and historical evidence.

The [development release](https://github.com/CutbackAI/cutback/releases/tag/v0.1.0.dev2) includes a `cutback-synthetic-dashboard.webm` walkthrough. Its banner identifies synthetic data. It shows the actual authenticated React/HTTP/PostgreSQL path, inventory, costs with an unknown model, opportunities, empty experiment/change states, instrumentation/security pages, mobile layout and credential clearing. It does not depict a live provider request or measured customer savings.

For the scanner → SDK → collector → database → report demonstration:

```sh
uv sync --locked --all-extras --dev
uv run python infra/compose/prepare.py
docker compose -f infra/compose/docker-compose.yml up -d --build
uv run python scripts/demo.py
```

The SDK uses synthetic provider transports. Collector authentication, persistence, duplicate handling, report retrieval and restart behavior use the actual local stack. The script emits a labeled receipt under `artifacts/`; no provider key is needed.

To reproduce the browser recording, install Node 24, run `npm ci --ignore-scripts` in `web`, regenerate OpenAPI/types, build the UI and install Playwright Chromium. With the local stack running, set `CUTBACK_TEST_BROWSER=true` and `CUTBACK_DEMO_VIDEO=true` in your shell, then run:

```sh
uv run python scripts/check.py tests/end_to_end/test_dashboard.py -q
```

The fixture creates a disposable test database and synthetic credentials. Recording is opt-in; ordinary CI does not record credentials. The recording appears under `web/test-results/`, and the screenshot under `artifacts/dashboard-overview.png`. No trace is recorded and the password field is masked. Use only the synthetic fixture for public recordings; never record real customer projects or credentials.

The dev2 [screenshot](assets/dashboard-synthetic.png) comes from the actual rebuilt browser test. Its release recording also exercises guided policy → CSV dataset → plan → exact budget approval → queue → cancellation. See [examples](../examples/README.md) for executable native clients and the complete approved synthetic experiment walkthrough. `scripts/scale_smoke.py` records bounded local ingestion/deduplication/query timings separately; those are not provider performance or customer savings measurements.
