# Reactive user example

From a source checkout, install the locked environment and run:

```shell
uv sync --locked --all-extras --dev
uv run python examples/reactive-user/app.py
```

This runs two independent synthetic applications through the pinned native SDK:
a ticket tool workflow and a policy retrieval workflow. After each scripted
conversation, a user simulator reacts to the actual observed answer, and the
target answers again. The original tools, permissions and assertions stay frozen.
The transport is a local scripted test double: no provider credentials, paid
requests, or network inference are used. Its outputs demonstrate the protocol,
not the quality of any real model.

For the HTTP collector, PostgreSQL ledger, encrypted evidence and dashboard:

```shell
uv run cutback demo --reactive-users
```

Docker and Git are required. Choose **Explore sample**, then **Inspect agent and
retrieval tasks**. Load execution receipts to compare `evaluation` and `simulation`
calls. Enter a purpose to read the authorized conversation. The simulator receipt
shows its request count, generated turns, known costs and stopping condition.

The demo's approvals authorize only synthetic fixture calls. A native provider
run requires a new exact plan approval and an operator-configured provider key.
See [the execution contract](../../docs/reactive-users.md) before using real data.
