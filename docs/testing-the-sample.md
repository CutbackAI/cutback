# Test Cutback with the support-desk sample

This walkthrough uses a complete, independent application with owned synthetic tickets. No private application, provider key, paid inference, GPU or cloud account is needed. Docker runs only a temporary local PostgreSQL container. The collector, SDK instrumentation, database, approvals and worker are actual Cutback components.

## Install and start

Use Python 3.11–3.13 in a fresh virtual environment and start Docker Desktop with Linux containers. No Node setup or provider key is needed.

```sh
python -m pip install "cutback[server,openai]==0.1.0.dev4"
cutback demo
```

For source development, use uv 0.13.6, clone the repository, run `uv sync --locked --extra server --extra openai`, then `uv run cutback demo`. The built dashboard ships in the package.

The first Docker run may download the pinned PostgreSQL image. The launcher creates its own database container and random scoped credentials. It never connects to your application or an existing customer database. Wait for `Ready:` and the browser to open. If the port is occupied, use `cutback demo --port 8877`; `--no-open-browser` prints the address without opening a tab.

## What to check in the browser

1. Click **Explore sample**. The comparison loads directly: **compact: Passes sample gates**, **unsafe: Not eligible**, with failed `priority`. These are synthetic fixture decisions, not measured model or customer performance. The short-lived scoped key stays in page memory.
2. Open **Inventory**. One shared `_analyze` SDK site belongs to the `triage` and `needs_escalation` entry workflows. Its application observations total **12**. Missing observations mean unknown usage, not zero.
3. Open **Costs**, choose **Application calls** under Traffic purpose and group by **workflow**. Two named source workflows reconcile to those 12 calls. Evaluation and judge traffic have separate filters. Unknown/legacy traffic is never relabeled as application traffic. Model costs are recorded-catalog estimates, not invoices.
4. Return to **Sample**. Read the scorecards and segment breakdowns. The compact candidate retains all fixture labels; unsafe loses urgent priorities. Required failures override a favorable average or lower modeled cost. A required criterion's sample/group limits and baseline regression gate are enforced separately.
5. Under encrypted evidence, enter `sample-review` and click **Read authorized case outputs**. The default shows failed cases, with baseline, compact and unsafe output side by side on the same input. Use candidate/segment filters and pagination. Each read requires current policy, admin scope and an audit receipt. Older unindexed outputs have a separate browser and no claimed failure coverage.
6. Expand **Review or correct this label**, enter a correction review reference and create a corrected dataset. This creates a new version and preserves the original run. Independent-label and representativeness assumptions require review again; a new experiment needs new approval. Do not check the all-labels attestation unless you actually performed that review.
7. Click **Inspect technical-only result**. Its primary label must say **Technical checks passed; semantic quality unknown**, and missing intervals must render **Unknown**. Technical success does not verify semantic quality.
8. Click **Review source change**. In Changes, enter a purpose for the encrypted source change, then select **Read authorized diff and rollback**. Review the model-literal change, source/commit attestation, reverse diff and staging steps. Neither diff is applied or deployed. The unsafe candidate's acceptance control is disabled and the server rejects acceptance too.
9. Back in Sample, expand **How the sample works** to inspect source discovery and separately approved smoke/pilot/full receipts. Changed contracts, incomplete billing, failed predecessors and expired content block progression. A crashed billable operation is never automatically replayed.
10. Try phone width, then reload. Choose **Explore sample** again: credentials are absent from localStorage, sessionStorage and the URL. Stop with Ctrl+C when done; only the demo's own container/database and temporary ciphertext are removed.

## Files you can inspect

While running, the workspace stays available. The launcher writes safe metadata to `.cutback/support-desk-demo/sample-report.json` and a real editable experiment definition to `.cutback/support-desk-demo/experiment.yaml`.

```sh
cutback compile-experiment .cutback/support-desk-demo/experiment.yaml .cutback/reviewed-plan.json
```

Compilation is offline and makes no calls. It refuses to overwrite an existing destination. Submitting a modified experiment still requires server validation and a new exact-hash approval. The demo has no background provider worker and does not borrow your provider credentials.

To inspect a source-only sample without Docker:

```sh
cutback sample support-desk
cutback scan support-desk
cutback opportunities --target support-desk
cutback opportunities --target support-desk --json
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

For the reviewed Linux image, `python scripts/image_acceptance.py --image YOUR_REVIEWED_LOCAL_IMAGE` creates two disposable databases on the owned local Compose PostgreSQL service, runs the complete synthetic sample as UID 10001, restores an independently encrypted dump, copies encrypted objects and rotates their keys while retaining database pointers and expiry. It removes only its own resources. This is a local recovery drill, not a remote production recovery claim.
