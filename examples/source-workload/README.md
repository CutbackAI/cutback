# An application the agent can match to observed requests

[`app.py`](app.py) is an independent synthetic classifier. Its default HTTP
transport cannot reach a vendor. The walkthrough creates a committed temporary
copy, supplies fixed synthetic replies, captures eight actual native SDK calls
and lets the agent find a matching retained analysis.

With the owned local collector and profiling worker configured as in
[request reuse](../request-reuse/README.md), run from the source repository:

```console
python examples/request-reuse/demo.py --endpoint http://127.0.0.1:8080 --source-workload
```

Expect eight synthetic native calls, three modeled potential hits, zero paid
provider calls and unchanged application source. The agent's positive decision
supports a separate trial; it grants no execution or source-change authority.
The driver removes its temporary app and deletes its synthetic collector project
by default. See [the complete walkthrough](../../docs/source-workload.md).
