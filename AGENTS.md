# Working on Cutback

This repository implements a Python-first Apache-2.0 product. Keep the scanner offline, parse-only and independent from optional runtime/server/evaluation dependencies. Metadata is the default; payloads belong in the explicitly authorized encrypted store. Use Decimal for money and preserve unknown values.

For SDK changes, inspect the pinned installed SDK and extend native-response, sync/async, stream, retry, outage and privacy tests before widening the support matrix. Tests use synthetic transports; paid requests and production deployment require explicit authorization.

For server changes, run real PostgreSQL migrations and tenant/RBAC tests. Preserve released migrations, scoped credentials, bounded ingestion, shared quotas, and pre-call experiment reservations. A crashed billable operation is not safe to retry automatically.

For dashboard changes, regenerate OpenAPI/types, build React, and run the authenticated browser/accessibility test. Browser credentials stay in memory. Retained summaries cannot reconstruct missing quantiles or unknown coverage.

See `docs/progress.md` for current acceptance evidence and external gates, `docs/providers.md` for adapter limits, and `docs/evaluation.md` for evidence semantics. Finish authorized engineering work before requesting external decisions. Never convert a synthetic result into a real customer claim.

This is a public product repository. Use independent synthetic applications in examples and tests. Keep private application names, repositories, source, deployment details and integration notes outside publication inputs. Application-specific findings belong in that application's ignored workspace. Check publication files/history/archives with `scripts/check_public_boundary.py`; private marker policies stay outside the repository and never enter source or logs.
