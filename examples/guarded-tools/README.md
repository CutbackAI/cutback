# A tool writes a record; its response is withheld

This independent source sample makes the effect visible: a scripted native agent
reads an authorized record and applies an approved `open → resolved` SQL
transition. A separate checker blocks the resulting response. The write remains
recorded, the reply is withheld, further model turns stop and the tool container
is cleaned up. There are no provider API calls.

Use a source-matched checkout with Docker Desktop running Linux containers,
Python, Git and uv. This feature is not in the published PyPI development wheel.

```powershell
uv sync --locked --all-extras --dev
docker build --tag cutback-owned-tools --file infra/compose/Dockerfile .
if ($LASTEXITCODE -ne 0) { throw "Local image build failed" }
$toolImage = docker image inspect cutback-owned-tools --format '{{.Id}}'
if ($LASTEXITCODE -ne 0) { throw "Local image identity unavailable" }
uv run cutback demo --guarded-tools --tool-image-id $toolImage
```

1. Open the local dashboard address printed by the command and choose **Explore
   sample**. The command owns a temporary local database; leave its terminal open.
2. In **Experiments**, open the isolated record experiment's reproducible
   evidence. Load isolated tool receipts and inspect a session's operations.
3. Compare the read and patch rows. The patch has a recorded state change and a
   returned broker receipt, while its reply is withheld. Release authorization
   and consumption are different facts. All sessions require confirmed cleanup.
4. Open **Runtime guardrails**. The synthetic policy is disabled after the sample
   completes; retained approval is not current execution authority. Its private
   rules are available only through purpose-authorized administrator review.
5. Stop the command with Ctrl+C. It removes its temporary database. Its sample
   workspace is local and must not be committed into another application's repo.

The model responses are scripted, while native SDK parsing, separate checker
and broker processes, Docker isolation, SQL/CAS, PostgreSQL receipts and response
quarantine are real. This does not demonstrate production application changes,
real-model quality or complete task cost. See [the execution
contract](../../docs/guarded-tools.md) and [ordinary isolated
tools](../isolated-tools/README.md) for the unblocked sample.
