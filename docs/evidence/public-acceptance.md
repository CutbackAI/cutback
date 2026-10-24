# Acceptance evidence

Dev2 local engineering acceptance, 24 October 2026. Release publication is recorded separately in the PyPI verification receipt.

- Full synthetic/native PostgreSQL suite: **153 passed, four legacy-version cases skipped**. Authenticated React browser, axe/mobile, guided policy/dataset/plan/approval/queue/cancellation and actual RSA/JWKS/PKCE fixtures ran.
- Separate native OpenAI 1.55.3/Pydantic 2.10.6 compatibility/scanner suite: **21 passed**.
- Native Node SDK: **9 passed**, plus TypeScript compatibility. Dashboard file parser: **3 passed**.
- Isolated native CPU HF/PEFT optimizer, encrypted artifact download/checksum/expiry, cancellation and crashed lease tests: **3 passed** again after auditing/upgrading Torch to 2.14.1+cpu and setuptools to 84.2.1.
- Helm private defaults and opt-in manifests: **3 passed**, using strict Kubernetes 1.35 schemas. Terraform fmt/validate and **one mocked plan passed** without a cluster.
- Final local Compose upgrade reaches schema **0004**. Encrypted backup/restore matched all **33 tables** in a separate disposable database.
- Full synthetic experiment walkthrough: 64 observations and two paired worker reports via native SDK transports, actual HTTP/PostgreSQL and authorized encrypted datasets; own project/artifacts deleted afterward.
- Local capacity smoke: **10,000 durable events plus 10,000 correctly deduplicated replays**. Ingestion/replay took 24.38 seconds; usage query took 664 ms on Windows/Docker. Unknown total and exact known subtotal were preserved. This is a bounded local smoke, not a production SLA.
- Ruff, formatting, mypy, generated OpenAPI/TypeScript/React build, archive checks, Twine metadata and core-only wheel install/offline scan passed. Runtime/training and web/Node audits reported zero known vulnerabilities in checked dependencies. SBOMs accompany the release.

Synthetic results are not independent customer quality, realized savings, paid-provider, customer IdP/Stripe/S3, CUDA or live-cluster acceptance. No production deployment, paid inference/GPU operation, customer payload capture or live model change occurred. See [progress](../progress.md), [limitations](../limitations.md) and [completion ledger](../product-completion.md).

The unchanged upstream OpenAI examples at commit `acd0c54d8a68efeedde0e5b4e6c310eef1ce7867` were scanned offline: 32 Python files, four supported call sites, zero source modifications, unknown spend. The optional Linux CPU training container built and imported native Torch/Transformers/PEFT/safetensors with nonroot/read-only settings and network disabled. These are separate from live provider or CUDA acceptance.

Hosted [CI](https://github.com/CutbackAI/cutback/actions/runs/40088987906) and [security checks](https://github.com/CutbackAI/cutback/actions/runs/40088987920) passed. Python 3.11: 153 passed/four legacy skips, including native CPU training; Python 3.13: 152 passed/five skips. Both separate legacy runs passed 21. CodeQL: zero open alerts.

Published [dev2](https://pypi.org/project/cutback/0.1.0.dev2/) through [Trusted Publishing](https://github.com/CutbackAI/cutback/actions/runs/40089189222), which reran acceptance successfully. A fresh real-index scanner-only install and offline scan passed. Downloaded wheel/source SHA-256 values match PyPI. `pypi-attestations==0.0.30` cryptographically verified both distributions against the CutbackAI/cutback repository identity. Exact hashes and URLs are in [the verification receipt](../pypi-release-verification.json).
