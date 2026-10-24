# Acceptance evidence

Published pre-alpha 0.1.0.dev1; 24 October 2026.

Local PostgreSQL/synthetic SDK/browser suite: 104 passed, 4 legacy-version cases skipped intentionally. Separate OpenAI 1.55.3/Pydantic 2.10.6 compatibility and scanner suite: 21 passed. Statement coverage: 80.45%.

React/TypeScript build and browser/API parity, mobile overflow, credential lifetime and axe smoke passed. The local encrypted backup restored into a disposable database with matching row counts across 22 tables. Core-only wheel install, offline scan, archive exclusions, Ruff and mypy passed. Python runtime audit and npm audit reported zero known vulnerabilities in the checked environment.

Inference and capture fixtures are synthetic. No paid provider call, customer payload capture or production deployment occurred. Hosted Python 3.11 and 3.13 each passed 104 main tests (four legacy cases intentionally skipped) plus 21 separate legacy tests. The [publishing run](https://github.com/CutbackAI/cutback/actions/runs/40078866445) passed, as did [security checks](https://github.com/CutbackAI/cutback/actions/runs/40078860136), with zero open CodeQL alerts. A fresh real-index PyPI installation and offline scan passed; downloaded release artifact hashes match PyPI. The 0001 to 0002 upgrade drill retained existing metadata/key digests and accepted the previous event schema. Live provider/cloud acceptance remains pending. See progress.md, providers.md and limitations.md for scope and external gates.
