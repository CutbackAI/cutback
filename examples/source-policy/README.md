# An independent source-policy application

The application classifies synthetic tickets as `Urgent` or `Normal`. Its native
client is owned by the caller; `app.py` loads a fixed `policy.json` and returns
the actual SDK response. The consumer is parsed in full before source-bound
optimization. It is deliberately narrow so a proposed configuration cannot
silently add tools, redirects, state or another input contract.

From a current Cutback source environment:

```sh
uv run cutback source-policy sample ../independent-policy-application
uv run python ../independent-policy-application/check_application.py
```

The check uses a closed HTTP transport and native SDK parsing. No network call
or provider credential is needed. It exercises every supplied case, caller-owned
retries, response types, the exact selected request, and rejection beyond the
fixed input byte bound.

For the async consumer:

```sh
uv run cutback source-policy sample ../independent-async-policy-application --async
uv run python ../independent-async-policy-application/check_application.py
```

`async_app.py` shows the exact awaited producer; its check uses the actual pinned
`AsyncOpenAI` client with closed synthetic HTTP. It borrows the client and
preserves native cancellation and retry behavior. Async caching and streams are
not qualified. Use the same prepare/approve/check/rollback commands for a clean,
tracked copy of this producer and its configuration.

For the whole collector/database/agent/change workflow, run this from the Cutback
checkout:

```sh
uv sync --locked --extra server --extra openai
uv run cutback demo --source-policy
```

Choose **Explore sample**, then **Automatic improvements**. Read the failed simple
alternatives, training/validation/confirmation receipts, whole-budget accounting,
checked source copies and inverse rollback. The original app is unchanged.
Synthetic outcomes demonstrate mechanics; real model quality, real customer
savings and deployment permission are not established.

[The step-by-step guide](../../docs/source-policy-programs.md) explains permitted
real data, approval, recovery, review, cache runtime requirements and current
unsupported application contracts. Published PyPI dev5 lacks this source feature.
