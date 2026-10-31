# Native response cache, with an actual encrypted collector

This source-checkout lesson sends four synthetic requests through the pinned
native SDK and the existing PostgreSQL-backed cache API. The first two misses
call a closed `httpx.MockTransport`; the two repeats read encrypted responses.
No provider key, provider account, model download or paid inference is needed.

Start Docker Desktop, then run from the Cutback source checkout:

```sh
uv run --locked --extra server --extra openai python examples/native-response-cache/app.py
```

The script explicitly authorizes synthetic content retention in its own local
collector on port 8097. It creates/reconciles the same project, scoped keys and
policy using private idempotent journals. Raw native response bodies are stored
only in the encrypted artifact store. Requests are keyed with a private HMAC;
lookup metadata contains no input or instruction text. No credentials or response
bodies are printed.

The summary should show four requests, at least two hits and at most two native
fixture requests. Repeating the lesson can reuse the existing cache within its
60-second TTL. A policy or credential that expired, was revoked or conflicts with
the saved identity stops; do not clear the journals to bypass that stop.

Cached SDK usage describes the original response. It is not another provider
meter. Missing usage—including new SDK fields omitted by a server—stays unknown.
The result keeps invoice cost, whole-task cost and real model quality unverified.
It does not establish population hit rate or customer savings.

Stop the owned collector while preserving its volume and encrypted artifacts:

```sh
uv run --locked --extra server --extra openai python examples/native-response-cache/app.py down
```

The current adapter is deliberately explicit: synchronous stateless Responses
text with `store=False`, a bounded output, a fixed provider credential and a
zero-retry client. It refuses streams, asynchronous clients, tools, conversation
state and unreviewed destinations before I/O. Lookup/policy failures dispatch no
native call. A write failure after a successful native call returns that original
response and its write status; it never retries the provider. Concurrent misses
are not coalesced. Cache availability, CPU/hosting costs and mutable task state
need separate workload acceptance.

This is a source component under qualification. It is not yet connected to the
automatic cross-engine selector, and does not close that roadmap requirement.
