# Captured-output quality, with no provider API key

This independent synthetic application emits two technically successful answers. One answer is empty. The online quality worker should report one passed and one failed **nonempty-output contract**. Neither result claims that the answer solved a real user's task.

1. Use an isolated synthetic project in your local PostgreSQL-backed collector, with an admin control key and a staging ingest key. See [local setup](../../docs/quickstart.md). Explicitly configure encrypted payload storage and `CUTBACK_ALLOW_PAYLOADS=true` on the collector and worker. They must share the same encrypted store and encryption key. The default metadata-only Compose configuration deliberately does not enable this.
2. Set `CUTBACK_ENDPOINT`, `CUTBACK_PROJECT_ID`, `CUTBACK_CONTROL_KEY`, and `CUTBACK_INGEST_KEY` in your process environment. Keep keys out of source and transcripts.
3. Run `python examples/online-quality/sample.py`. This creates a one-hour rule, a scoped synthetic capture policy, two metadata spans, and two encrypted captured outputs. It approves **zero provider requests and zero provider dollars**.
4. Enable the worker's `CUTBACK_ONLINE_EXECUTION=true` and `CUTBACK_QUALITY_EXECUTION=true`. Keep experiment, training and webhook execution disabled. No provider credential is needed for this deterministic rule.
5. Open **Monitoring → Online content quality** in the authenticated dashboard. Refresh or reopen Monitoring, inspect the rule, and verify one passed receipt, one failed receipt, zero reserved requests and zero known spend. Investigate the source trace: both steps have technical status `ok`, while the empty answer fails the content contract.
6. Compare two explicit nonoverlapping observed windows. Empty windows and absent category features stay unknown. Pause the rule or delete the isolated project when finished.

For pinned JSON schemas, model-judge approvals, provenance, budgets, retention and limitations, see [online quality](../../docs/online-quality.md). This sample never enables a model judge, runs external inference, configures notifications or modifies a private application.
