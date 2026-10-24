# Contributing

Use Python 3.11+, uv, Node 24 and PostgreSQL 17 for the full suite.

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
