# Local service qualification

From a source checkout, review [profile.json](profile.json), then follow [the operator workflow and limits](../../docs/load-qualification.md). The CLI measures actual owned-loopback HTTP/PostgreSQL ingestion, replay, trace/usage queries and scoped viewer denials using two independent synthetic tenants. It performs zero provider calls and requires no payload capture.

Local acceptance is recorded in [progress](../../docs/progress.md); hosted/publication gates are separate. This is not in the published dev5 package. The example thresholds are a declared local test envelope, not a production SLA or customer benchmark. Receipts remain private under the target's ignored `.cutback/qualification/` directory; failures and unconfirmed cleanup remain visible.

To compare two explicitly started owned local collectors, run `compare.py --before-target BEFORE_WORKSPACE --after-target AFTER_WORKSPACE --execute-local` from the source tool environment. It uses the same profile in before/after/after/before order. Retain the exact code/dependency/hardware context yourself; neither the labels nor a receipt hash independently attest it. The runner saves completed trials, stops on unconfirmed cleanup and does not publish results or change either service's code/configuration.
