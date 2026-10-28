# Isolated provider compatibility

The main lock and `cutback[openai]` keep OpenAI 2.29.0. The `openai3` project separately locks OpenAI 3.35.1 and its native HTTPX2 transport without overriding an application's dependencies or changing the default provider extra. Install `cutback[instrumentation]` alongside an application's existing **exact qualified** SDK pin for observation. An optimizer/collector can use its own isolated environment and optional extras. Other SDK versions still abstain; this is not support for an open-ended major-version range.

From the repository root, run the same synthetic qualification used by CI:

```sh
uv sync --project infra/sdk-compat/openai3 --locked --python 3.11.18
uv run --project infra/sdk-compat/openai3 --locked python scripts/check.py -q \
  tests/unit tests/contract/test_migrations.py \
  tests/integration/test_native_episodes.py tests/integration/test_semantic_episodes.py \
  tests/integration/test_semantic_episode_controls.py tests/integration/test_episode_lifecycle.py \
  tests/integration/test_execution_ledger.py tests/integration/test_campaign_workflow.py \
  tests/integration/test_campaign_preparation_basis.py \
  tests/integration/test_quality_native.py tests/integration/test_agent_workflow.py
```

Use an owned loopback PostgreSQL test destination through `CUTBACK_TEST_DATABASE_URL`, or the root `scripts/check.py` loader for the existing owned Compose secret. Each run creates a disposable database; do not point tests at a remote or production server. The test process intercepts every provider request, makes no paid call and uses synthetic data. CI repeats this on Python 3.11.18 and 3.13.18. The local compatibility project installs a built Cutback distribution, not an editable import.

Qualification includes native sync/async responses and streams, physical retry identity, partial-stream closure, cancellation, outages, bounded exporter loss, encrypted capture rights, structured text and fixture tool turns, worker logging/privacy, real migrations and campaign approval/accounting. Both native HTTPX2 clients and the SDK's legacy HTTPX compatibility path are exercised. A positive cache-write count with no matching qualified contract stays unpriced. The source-only [modern observation contract](../../docs/modern-model-accounting.md) adds disjoint Luna cache-write metadata and checked prices, separately from paid target qualification. SDK compatibility alone does not qualify new paid targets or arbitrary reasoning. The separate [native Luna text contract](../../docs/native-modern-text.md) binds its approved request and exact price reference. No real provider behavior or remote deployment is claimed. See [provider scope](../../docs/providers.md) and [executed acceptance](../../docs/progress.md).
