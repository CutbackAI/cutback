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

`observe_application.py` is a separate process entry point for seven synthetic
native calls, including repeated inputs. It prints observation/flush health when
you explicitly configure a collector and review the external bootstrap. It
captures metadata by default. Keep the fixed consumer unchanged; inserting
observation into `app.py` invalidates its full-program contract. The guide also
explains the read-only `source-policy assess-workload` command for separately
authorized encrypted request-profile evidence. A recommendation cannot activate
cache, grant spend or claim production savings. Async/multi-model native profile
assessment remains unqualified.

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
## Automatic data selection

With a matching collector and current scoped credential, run
`cutback source-policy discover --target . --producer app.py` to inspect bounded
compatible dataset and task-cost metadata. Preparation can omit `--dataset` and
`--baseline-snapshot` when there is exactly one compatible choice in the selected
environment. It refreshes the selected report and retains those IDs on retries.
Ambiguous versions, unknown costs and incomplete listings require an explicit
choice or the missing evidence. No examples are decrypted and no experiment is
approved by discovery. See the [whole-budget walkthrough](../../docs/source-policy-programs.md).
