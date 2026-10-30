# An agent that resolves a record

The task is concrete: read the permitted fields of a support record, change
`status` from `open` to `resolved`, then verify that its version advanced once.
Cross-project and cross-environment control records must remain unchanged.
Hidden notes must never enter model requests or metadata reports.

The native model replies are scripted so the walkthrough needs no provider key
and makes zero paid API calls. The tool service runs in a separate process; each
episode performs real SQL inside a contained Linux Docker container. HTTP,
encrypted storage, PostgreSQL reservations and cleanup receipts are real.

This requires current source, Git, Docker with a local Linux daemon, and the
locked Python runtime. The published PyPI dev5 package predates this example.
From the repository root, build the local tool image once:

```powershell
docker build --file infra/compose/Dockerfile --tag cutback-tools:local .
$cutbackToolImage = docker image inspect cutback-tools:local --format '{{.Id}}'
uv run --locked --extra server --extra openai cutback demo --tool-image-id $cutbackToolImage
```

On a POSIX shell:

```sh
docker build --file infra/compose/Dockerfile --tag cutback-tools:local .
cutback_tool_image=$(docker image inspect cutback-tools:local --format '{{.Id}}')
uv run --locked --extra server --extra openai cutback demo --tool-image-id "$cutback_tool_image"
```

Choose **Explore sample**, then **Inspect isolated record tasks**. In the result,
choose **Load isolated tool receipts**. The reference and compact model each run
two owned cases: four sessions, eight tool operations and four state changes.
Every session must show **Cleanup confirmed**. **Inspect operations** shows the
read and approved transition. Model charges are displayed separately; complete
task cost remains unknown. The sample is too small and synthetic to establish
model superiority or customer savings.

To inspect the actual returned fields and final state, use the existing
purpose-authorized encrypted case evidence view. Clear it when finished. The
normal receipts contain no record payload. The experiment form recognizes this
dataset as an isolated record task and requires the operator's broker, owner,
image and content-use contract before preparing another plan. Preparation does
not approve a budget or execute anything.

Press Ctrl+C to stop the temporary sample database. The launcher confirms its
owned tool sessions are contained and stops its separately launched broker. It
does not connect to a production application or use an ambient provider key.
The [operator guide](../../docs/isolated-tools.md) explains consent, limits,
outages, recovery and the commands for an independently operated installation.
