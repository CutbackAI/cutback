# Development status

Version 0.1.0.dev0 is an initial pre-alpha foundation.

| Area | Status |
|---|---|
| Offline Python scanner | Initial OpenAI/Anthropic corpus; conservative resolution |
| Runtime observation | Experimental OpenAI 2.29.0, synthetic sync/async/stream tests |
| Collector and PostgreSQL | Auth, scope, idempotency, migrations and reports |
| Self-host deployment | Local Compose tested; production operations incomplete |
| Pricing | Small offline text seed; precise modeled costs; unknown stays null |
| Experiments, verified quality, optimizer and dashboard | Not implemented |

Local foundation evidence: 69 tests passed, 84.61% statement coverage. All provider responses in ordinary tests are synthetic. Live provider, real staging, per-retry attempt accounting, production retention, backup/restore, broader scanner coverage and independent onboarding remain open. No production readiness or verified customer savings is claimed.

Run the commands in CONTRIBUTING.md to reproduce validation. See limitations.md and providers.md for unsupported behavior.

First development release: https://pypi.org/project/cutback/0.1.0.dev0/

Hosted release checks passed 69 tests on both Python 3.11 and 3.13. Publishing provenance and fresh-index installation are recorded in pypi-release-verification.json.
