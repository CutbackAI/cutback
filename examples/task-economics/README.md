# What does a successful task actually cost?

Use a source checkout; PyPI dev5 does not contain this working increment. This independent example creates a new synthetic project. Its seven actual OpenAI SDK calls use an owned `httpx.MockTransport`; no model request reaches a provider and no API key is needed.

With Docker Desktop running, from the Cutback checkout:

```sh
uv sync --locked --all-extras --dev
uv run cutback collector up --target . --port 8085
uv run python examples/task-economics/sample.py --local-target .
```

The sample reads the owned local collector credential into memory. It prints the synthetic project ID and dashboard link, never the key. Connect using your authorized collector credential. For an independently configured loopback collector, use `--endpoint http://127.0.0.1:8085` and supply `CUTBACK_CONTROL_TOKEN` through your shell's secret mechanism.

In **Task economics**:

1. Set **Application release** to `synthetic-reference`, **Expected tasks in window** to `2`, then read the report. Both declared tasks succeed; each makes two native calls. Freeze the evidence.
2. Select `synthetic-cheap-but-worse`, keep the expected count at `2`, and read/freeze again. One task fails even though its SDK/HTTP response succeeds. Its spend is included in cost per successful task.
3. Open **Compare frozen releases and optimization costs**. Choose those two receipts. This sample performs no optimization runs; explicitly acknowledge that empty run list and no other optimization costs. For this tiny synthetic demonstration, set minimum tasks to `2` and a forecast of `100` successful tasks. Record the comparison. It must reject the declared outcome regression despite the lower modeled cost.
4. Select `synthetic-incomplete`, expected tasks `1`. The tool price is unknown: the native subtotal remains visible while full task cost stays unknown. Leaving expected task count blank also leaves window coverage unknown.

All token counts, answers, task results and modeled rates are synthetic. The two-task override demonstrates a branch; it is not an adequate customer validation sample. Frozen observations authorize no inference, source application, traffic change or deployment.

`--cleanup` removes the project created by that invocation after exercising it. To stop the owned collector while preserving its local database, run `uv run cutback collector down --target .`. [Contracts and integration](../../docs/task-economics.md).
