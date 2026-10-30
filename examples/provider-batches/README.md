# Follow a native asynchronous batch lifecycle

From the source checkout:

```console
uv sync --locked --all-extras --dev
uv run cutback demo --provider-batch
```

Choose **Explore sample**, then **Inspect the native batch lifecycle**. See a completed three-category ticket task, native item reservations, descriptive validation metrics, modeled native costs and confirmed vendor-file deletion. The provider is an owned in-memory HTTP mock using the pinned SDK. PostgreSQL, approval, encrypted datasets and canonical accounting are real. No provider key, paid inference or deployment is involved.

For automatic instruction improvement alongside batch processing:

```console
uv run cutback demo --ticket-router --provider-batch
```

These are separate workflows and approvals. Agent confirmation can support a source proposal; batch validation cannot. A batch result also cannot report per-request latency.

The independent task and native provider fixture are `src/cutback/sample_app/ticket_fixture.py` and `src/cutback/sample_app/batch_provider.py`. The full control/worker walkthrough is `src/cutback/demo_provider_batches.py`. It has three labels, 240 unique inputs and 60 declared intent families. Its answers and token usage are synthetic, so a perfect sample score is not a real model-quality claim.

[Contracts, opt-in, limits and recovery](../../docs/provider-batches.md). This source increment is absent from the older published PyPI dev5 artifacts.
