# Contributing

Use the reviewed Python 3.11.18 or 3.13.18, uv 0.13.6, Node 24 and PostgreSQL 17 for the full suite.

```sh
uv sync --locked --all-extras --dev
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python scripts/check.py -q
uv build
uv run python scripts/wheel_smoke.py
```

In web: npm ci --ignore-scripts, then npm run types/build after scripts/export_openapi.py. Install Chromium and enable CUTBACK_TEST_BROWSER for browser/accessibility tests. Legacy compatibility runs separately with OpenAI 1.55.3/Pydantic 2.10.6; current tests must not silently substitute it.

Ordinary provider data is labeled synthetic. Live calls require vendor/data/budget approval. Preserve released migrations and extend tenant/privacy/native SDK contracts before widening support. Keep docs/evidence truthful. Contributions are Apache-2.0; retain notices and report vulnerabilities privately through SECURITY.md.

Node SDK: `npm ci --ignore-scripts`, `npm test`, `npm run check` in packages/js-sdk. Dashboard import parser: `npm run test:unit` in web. Helm 4 render contracts use strict Kubernetes schemas; Terraform 1.17 uses `init -backend=false`, `validate`, and mocked `test` without a cluster. See infra/README.md.

Native training acceptance needs the isolated `infra/training/uv.lock` runtime. Set `CUTBACK_TEST_TRAINING_PYTHON` to its Python executable; never install Torch into the scanner core. The test creates an owned random model locally and runs two CPU optimizer steps without downloading a model or using a paid GPU.

Start with a narrow contribution: an independent scanner fixture covering an unresolved binding, a native SDK counterexample inside the supported matrix, a paired-case accessibility improvement, or a clearer step in the sample walkthrough. Explain the expected observable behavior before changing code. Preserve parse-only core installation; do not install server/training dependencies merely to scan.

For a complete local check, prepare the owned Compose PostgreSQL instance with `python infra/compose/prepare.py`, then start its postgres service. `scripts/check.py` creates and removes a unique test database; it never mutates the configured base database. Set `CUTBACK_TEST_BROWSER=true` after installing Chromium to include authenticated desktop/mobile/axe/PKCE checks. See [operator recovery](docs/operating-and-recovering.md) for queue, retention, object and key acceptance. Hosted CI and security must pass on the exact PR revision before merge. A sole maintainer cannot provide independent peer review; passing checks do not make that claim.
