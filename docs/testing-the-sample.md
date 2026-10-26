# Test Cutback with the support-desk sample

This walkthrough uses a complete, independent application with owned synthetic tickets. No private application, provider key, paid inference, GPU or cloud account is needed. Docker runs only a temporary local PostgreSQL container. The collector, SDK instrumentation, database, approvals and worker are actual Cutback components.

## Start from a fresh checkout

Install Python 3.11–3.13 and uv, and start Docker Desktop (Linux containers). Node is needed only when developing the dashboard; the repository includes its built bundle.

```sh
git clone https://github.com/CutbackAI/cutback.git
cd cutback
uv sync --locked --extra server --extra openai
uv run cutback demo
```

The first Docker run may download the pinned PostgreSQL image. The launcher creates its own database container and random scoped credentials. It never connects to your application or an existing customer database. Allow the sample stages to finish; the terminal prints `Ready:` and opens the dashboard. If the port is occupied, use `uv run cutback demo --port 8877`. Use `--no-open-browser` to open the printed address manually.

## What to check in the browser

1. Click **Explore sample**. No key entry is needed for this isolated synthetic workspace. Its short-lived scoped key stays in page memory. Ordinary collectors retain their normal authentication.
2. Read the four-step story. **Inspect the scanned call** opens Inventory and points to `app.py`. **Inspect modeled usage** shows actual recorded SDK observations priced with the pinned catalog. These are fixture costs, not an invoice or a real-model benchmark.
3. Return to **Sample** and click **Compare sample candidates**. The baseline is GPT-4.1; the compact candidate uses GPT-4.1 mini. The `unsafe` variant deliberately ignores urgent priority. The compact candidate must show **Passes sample gates**; unsafe must show **Not eligible**, with `priority` listed as failed. No candidate is verified for customer quality.
4. Read the category and priority scorecards, segment breakdowns, paired case-score counts and modeled sample difference. Monthly savings remain unknown. A positive average cannot override a required criterion failure.
5. Under encrypted evidence, enter `sample-review` as the purpose and click **Read authorized case outputs**. Expand a ticket to inspect input, expected output, candidate output and criterion results. Use pagination. Each content read requires an active policy, admin scope and an audit receipt; metadata downloads do not contain these payloads.
6. Click **Inspect technical-only result**. The result must render **Unknown** for unavailable confidence intervals instead of crashing. Nonempty output is not semantic correctness.
7. Inspect **smoke**, **pilot** and **full** receipts. Early stages use bounded validation cases; full uses the held-out test split. Each has its own exact-hash approval. Changed contracts, incomplete billing or a failed predecessor block progression. A crashed billable operation is not automatically retried.
8. Expand the source diff and rollback. They change the sample's model literal and reverse that change. Neither is applied. Synthetic sample approval never authorizes a production deployment.
9. Try phone width. Tables scroll inside their containers. Reload the page: you must explicitly choose **Explore sample** again; credentials are not in localStorage, sessionStorage or the URL.

## Files you can inspect

While running, the workspace stays available. The launcher writes safe metadata to `.cutback/support-desk-demo/sample-report.json` and a real editable experiment definition to `.cutback/support-desk-demo/experiment.yaml`.

```sh
uv run cutback compile-experiment .cutback/support-desk-demo/experiment.yaml .cutback/reviewed-plan.json
```

Compilation is offline and makes no calls. It refuses to overwrite an existing destination. Submitting a modified experiment still requires server validation and a new exact-hash approval. The demo has no background provider worker and does not borrow your provider credentials.

To inspect a source-only sample without Docker:

```sh
uv run cutback sample support-desk
uv run cutback scan support-desk
uv run cutback opportunities --target support-desk
uv run cutback opportunities --target support-desk --json
```

`support-desk/app.py` depends only on a supplied native OpenAI client transport, not Cutback internals. The source-only path does not simulate a database or claim observed costs. No application code runs during scanning. `cases.jsonl` contains synthetic labels.

Press **Ctrl+C** in the demo terminal when finished. Its own temporary PostgreSQL container and encrypted temporary artifacts are removed; safe sample metadata remains in the ignored `.cutback/` directory. This does not stop your other containers or delete another project.

## Maintainer acceptance checks

```sh
uv sync --locked --all-extras --dev
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python scripts/export_openapi.py
```

In `web`, run `npm ci --ignore-scripts`, `npm run types`, `npm run build`, and `npx playwright install chromium`. Start the repository's local PostgreSQL using `infra/compose/prepare.py` and `docker compose -f infra/compose/docker-compose.yml up -d postgres`. Then, from the root, run `uv run python scripts/check.py -q`. Enable `CUTBACK_TEST_BROWSER=true` for the authenticated browser/axe/mobile tests. Tests use a unique disposable database and refuse remote database hosts. Native CPU training is a separate opt-in runtime as described in [local training](local-training.md).

See [application onboarding](application-onboarding.md) only after you understand the sample. Your application names, source and reports stay in that application's ignored workspace.
