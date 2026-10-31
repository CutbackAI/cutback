# A complete routing change in an independent application

The task is simple: return `Urgent` or `Normal` for an owned synthetic ticket.
`app.py` uses the caller's native OpenAI client and a one-baseline `routing.json`.
The evaluation fixture intentionally makes the small model fail some long tickets.
Cutback learns from training receipts, validates separately, confirms once on
untouched groups and prepares a checked configuration change.

No private application, provider key or paid request is needed for this example.
It requires the current source feature; published PyPI dev5 lacks it.

From the Cutback checkout:

```sh
uv sync --locked --extra server --extra openai
uv run cutback demo --source-routing
```

Choose **Explore sample**, then **Routing**. Inspect the three runs and the source
proposal. The demo also creates original/proposed independent Git applications,
runs `check_application.py` on both, checks byte-exact rollback and creates a
local review ref. Read `.cutback/support-desk-demo/routing-source-review.json` and
the adjacent change/check receipts. No remote app is changed or deployed.

To check the supplied baseline consumer alone, from this directory using the
source environment's Python:

```sh
python check_application.py
```

The transport is offline. The native SDK still parses the actual response type;
the check verifies input, instruction, bounds and `store=False`. Synthetic replies
are not a model benchmark. Caller-owned retries and native exception/response
semantics are preserved; only the documented pinned synchronous Responses
consumer is qualified.

For your own application, copy only the independent pattern after inspecting its
consumer and checks, keep SDK pins, use real permitted examples/labels and follow
the [six-step application-agent guide](../../docs/source-routing.md). Use current
collector permission and both-tree checks before reviewing the exact diff.
Hosting, fitting and full-task costs remain unknown. The ordinary literal model
editor cannot install a router.
