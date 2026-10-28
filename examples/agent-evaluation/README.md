# Support agent and RAG assistant

Two independent synthetic applications show why an answer string alone is insufficient. They contain no private application source or integrations.

From the repository:

```sh
uv sync --locked --all-extras --dev
uv run python examples/agent-evaluation/sample.py
```

Expected: `status` is `COMPLETED`, baseline contract score is `1`, intentionally wrong score is `0`, and eight native SDK requests use the scripted HTTP transport. No request reaches a vendor. Token counts, answers and modeled prices are fixtures, not real-model benchmarks.

The support agent must close a ticket exactly once and reach the declared final state. Both a direct close and lookup-then-close path are allowed. The RAG assistant must retrieve the policy, preserve relevance coverage, answer the question and cite an exact quotation. An unrelated document contains an injected instruction; tools outside the declared fixtures cannot execute.

For the real PostgreSQL/worker/dashboard journey:

```sh
uv run cutback demo
```

Choose **Explore sample → Inspect agent and retrieval tasks → Load execution receipts**. Read the encrypted trajectories with an explicit purpose. Revise a contract to create a new frozen dataset. Stop with Ctrl+C to remove the owned temporary database. Scripted plans carry `scripted_test_only`; a normal provider-key worker refuses to execute them.

The sample implementation is [cutback.demo_agents](../../src/cutback/demo_agents.py), packaged so the installed source-build demo uses the same code as the example. [Contracts, API, generation and limitations](../../docs/agent-evaluation.md). PyPI dev5 remains an earlier release; use a source checkout for this feature.
