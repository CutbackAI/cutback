# Acceptance evidence

Development candidate 0.1.0.dev1; 24 October 2026.

Local PostgreSQL/synthetic SDK/browser suite: 103 passed, 4 legacy-version cases skipped intentionally. Separate OpenAI 1.55.3/Pydantic 2.10.6 compatibility and scanner suite: 21 passed. Statement coverage: 80.41%.

React/TypeScript build and browser/API parity, mobile overflow, credential lifetime and axe smoke passed. The local encrypted backup restored into a disposable database with matching row counts across 22 tables. Core-only wheel install, offline scan, archive exclusions, Ruff and mypy passed. Python runtime audit and npm audit reported zero known vulnerabilities in the checked environment.

Inference and capture fixtures are synthetic. No paid provider call, customer payload capture or production deployment occurred. Remote CI for this candidate is recorded after its run; live provider/cloud acceptance remains pending. See progress.md, providers.md and limitations.md for scope and external gates.
