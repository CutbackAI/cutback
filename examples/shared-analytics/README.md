# Shared analytical dashboard example

This example requires the matching source checkout; PyPI dev5 does not include
the analytical dashboard API. It creates one metadata view over an already
configured local project. It neither generates application traffic nor runs a
provider request, judge, optimizer or customer application.

For a complete independent application first, run:

```sh
uv sync --locked --no-dev --extra server --extra openai
uv run --no-sync cutback demo
```

Choose **Explore sample**, then **Open cost workspace → Saved charts →
support-cost-and-reliability**. The demo uses actual native SDKs, HTTP and
PostgreSQL with closed synthetic provider transports. Its existing observations
populate the charts; synthetic values do not establish real model quality,
provider latency, customer coverage or savings. Ctrl+C cleans up its owned stack.

## Use an existing local project

Preview first; no key, collector connection or file write occurs:

```sh
uv run python examples/shared-analytics/sample.py
```

Select a matching collector on a numeric loopback address and an existing project
UUID. Supply a current scoped editor or administrator key through the runtime
environment `CUTBACK_CONTROL_TOKEN`, using your normal secret delivery. Do not
put its value in a command argument or receipt. Then run:

```sh
uv run python examples/shared-analytics/sample.py --execute-local --endpoint http://127.0.0.1:8766 --project PROJECT_UUID
```

The example checks the server contract, saves a four-panel definition and reads
its data. No inference or content-capture authority is granted. A blank project
correctly displays no observations and unknown coverage. For the same journey
with controls, use **Costs → Saved charts → Create analytical dashboard**.

The private journal defaults to `.cutback/shared-analytics.json`; its ignore rule
is maintained inside that private directory. It contains the exact metadata
definition, UUID and phase, never the key. A lost save reply is reconciled with
the same UUID/body. A lost data reply resumes the read. Pending intents expire
after one hour. Changed destinations, projects, definitions and deleted saved
identities stop with fixed diagnostics rather than silently starting over.

Once complete, rerunning returns the historical receipt with
`saved_receipt_not_rechecked` and zero network requests. Open the collector to
inspect current state. A separately intended dashboard needs a new private
receipt filename or an explicit new view through the UI.

Only an explicitly selected numeric loopback collector is supported by this
example. The [contract](../../docs/analytics-dashboards.md) documents scopes,
retention, missing values, concurrency, quotas and schema rollback.
