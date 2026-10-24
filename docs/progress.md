# Implementation progress

Updated 24 October 2026. Published pre-alpha: [`0.1.0.dev1`](https://pypi.org/project/cutback/0.1.0.dev1/). [Source release and synthetic demo](https://github.com/CutbackAI/cutback/releases/tag/v0.1.0.dev1). PyPI ownership was secured by `0.1.0.dev0`; published versions are listed at https://pypi.org/project/cutback/. This is a working pre-alpha, not production validation or completion of the later commercial roadmap.

| Milestone | Implemented | Remaining acceptance boundary |
|---|---|---|
| M0 | Apache-2.0 package/repository, lockfiles, CI, real PyPI publication, clean installs | Brand review and launch decision |
| M1 | Typed offline inventory, aliases/imports, nested ignores, factories/instances/lazy clients/argument flow, route hints, move reconciliation, resource bounds and annotated syntax benchmark | Arbitrary dynamic/framework code and exhaustive recall remain unknown |
| M2 | OpenAI 2.29.0, legacy 1.55.3 Chat, Anthropic 2.3.0 Messages; sync/async/native streams/retry attempts; OTel context; bounded exporter/flush; durable health; idempotent collector/PostgreSQL/report | Approved real provider and remote serverless acceptance |
| M3 | Scoped hashed expiring/revocable keys, RBAC, shared quotas, Compose collector/worker, TLS reference, retention/daily archives, project export/delete, encrypted backup/restore drill and file/S3 drivers | Customer remote HTTPS/DB/secrets/lifecycle configuration and clean-host acceptance |
| M4 | Decimal/cache/reasoning accounting, checked immutable catalog/imports, effective-dated contract rates, grouped reports and conservative source/observed hypotheses | Invoice reconciliation and unsupported rates/units remain unknown; candidates need evaluation |
| M5 | Scoped opt-in capture/redaction/encryption, fixture discovery/import, provenance/frozen hashes, duplicate/customer grouping and stratification, task inference, labels/F1/schema metrics, grouped uncertainty, optional blinded BYOK judging | Rights, representative examples, independent quality criteria and paid-call approval |
| M6 | One-run plan-hash approvals, durable pre-call reservations, queued worker/leases/cancellation, eligibility/quality gates, recommendation review, source-bound model patch/rollback and canary decisions | Real benchmarks and operator staging/canary approval; dynamic/provider/prompt migrations need inspected custom diffs |
| M7 | React/TS dashboard, generated OpenAPI/types, API-backed pages, filters/refresh/pagination/chart-table parity, memory-only credentials, browser/accessibility smoke tests and portable agent bundles and a labeled synthetic demo | Independent onboarding and actual discovery in each third-party agent client |
| M8 | Intentionally after validated demand | TS/JS, broader providers, enterprise identity, large-scale storage/Helm/IaC, advanced routing/fine-tuning, hosted cloud and billing |

Executed evidence is in evidence/public-acceptance.md and its manifest. Ordinary inference is synthetic. No production deployment, paid inference, customer payload capture, live model change or commercial infrastructure provision has occurred. Capture tests use only synthetic fixtures.

Reproduce: `uv sync --locked --all-extras --dev`; Ruff check/format; mypy; `scripts/check.py`; `scripts/scanner_benchmark.py`; `scripts/backup_restore.py --drill`; build and wheel smoke. Browser tests additionally need npm dependencies/Chromium and `CUTBACK_TEST_BROWSER=true`. Legacy SDK cases run separately with OpenAI 1.55.3/Pydantic 2.10.6 rather than substituting SDK versions.

External decisions: approve an isolated staging collector and application diff; authorize a small real provider request with permitted data/vendor/dollar cap; supply rights-cleared examples and meaningful independent quality criteria; review staging evidence before any canary/production change; complete brand review before major launch. Existing approvals are reused within their scope.
