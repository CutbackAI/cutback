# Audit remediation and acceptance

The 26 October review found defects beyond the existing dev3 regression suite. This ledger tracks the engineering response. Existing PyPI files and released migrations stay immutable. Synthetic acceptance is not customer or production validation.

| Audit | Work | Acceptance status |
|---|---|---|
| A01 | Patched installer, interpreter and image/toolchain pins | Patched and checked locally; reviewed Linux image executed |
| A02 | Shared global capture deny, redaction and current policy enforcement | Regression-tested global, field-level and current-policy enforcement |
| A03 | Every required criterion's sample/group/confidence/regression gates | Required counts/groups/regression gates and server-side review denial tested |
| A04 | Durable blocked jobs, fair eligible queue, safe recovery | Real PostgreSQL fairness, blocked recovery and tenant denial tested |
| A05 | Context-managed SDK bindings and realistic scanner corpus | Context-managed native clients and source-map regressions passed |
| A06 | Source/workflow attribution and lifecycle-aware bootstrap | Two real sample workflows share one SDK site; source-linked usage and lifecycle tested |
| A07 | Incomplete judge votes invalid with explicit reasons | Parseable incomplete judge outputs rejected in regressions |
| A08 | Package-index-safe versioned links and images | Versioned absolute package links; wheel metadata/archive check passed |
| A09 | Named experiments and readable evidence/change decisions | Named plans/runs, readable decisions and encrypted source-diff review tested |
| A10 | Failure-first paired cases, filters and immutable corrections | Paired failures, filters, legacy reads and new correction versions tested |
| A11 | Application/evaluation/judge traffic separated and reconciled | Purpose-separated raw/archive totals reconcile; external spans identified |
| A12 | Evidence-tier-specific primary result wording | Technical-only primary wording and unknown intervals tested in browser |
| A13 | Integration diagnostics, clear CLI opportunities, consistent docs | Guided diagnostics, fresh zero counters, final health and CLI grouping implemented |
| A14 | Enforced repository/release and secret protections | Secret switches, required PR checks, main protection and immutable tags verified via API |
| A15 | Exact-release security workflow dependencies and toolchain checks | Publish depends on exact-revision CI and security; toolchain regression added |
| A16 | Maintenance isolation and bounded load/recovery acceptance | Failure isolation, 100001-row retention and concurrent two-tenant Linux smoke passed; no production SLA claimed |
| A17 | Supported ecosystem/coexistence and agent-client qualification | Scoped OTLP JSON profile, native/OTel context and overlap refusal tested; actual third-party clients remain external qualification |
| A18 | Adversarial regressions and independent consumer acceptance | Adversarial regressions, independent wheel consumer and native framework examples passed locally |
| A19 | Object/key recovery and clean-host acceptance | Linux database/object/key recovery passed; remote S3/PITR/IdP/cluster acceptance remains external |
| A20 | Outcome-focused public onboarding and contributor path | Independent sample, clear onboarding, examples and contributor templates implemented; demand, brand and fundraising remain external |

Engineering completion requires executed tests, generated API/types, a built dashboard, authenticated browser/accessibility acceptance, real PostgreSQL migrations and tenant tests, release/archive privacy checks, a fresh built-wheel consumer and an independent synthetic multi-workflow application. External user studies, paid provider benchmarks, customer data rights and production deployment cannot be manufactured by tests.

Local execution is recorded in [dev4 acceptance](evidence/dev4-acceptance.json), with fresh browser screenshots and an operator-run image/recovery script. Hosted checks and publication are separate gates; a local pass is not represented as a remote CI result. No paid inference, customer payload capture or production deployment occurred. Native SDK support remains the documented exact matrix; broader SDK support requires qualification, not an untested compatibility claim.
