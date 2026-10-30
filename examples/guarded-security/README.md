# Can the guarded agent still finish useful work?

This independent source sample runs the paired security/utility comparison with
a separate runtime checker and an isolated SQL tool service. One scripted control
refuses untrusted record content; the other completes the authorized task. Both
run under the same frozen tool policy. The comparison reports useful work,
declared attacks, known policy decisions and check coverage separately.

Use a source-matched checkout, Python, Git, uv and running Docker Desktop with
Linux containers. No provider API key is needed. PyPI `0.1.0.dev5` does not contain
this source feature.

```powershell
uv sync --locked --all-extras --dev
docker build --tag cutback-owned-tools --file infra/compose/Dockerfile .
if ($LASTEXITCODE -ne 0) { throw "Local image build failed" }
$toolImage = docker image inspect cutback-owned-tools --format '{{.Id}}'
if ($LASTEXITCODE -ne 0) { throw "Local image identity unavailable" }
uv run cutback demo --guarded-security --tool-image-id $toolImage
```

1. Open the printed local dashboard and choose **Explore sample**. Leave the
   terminal running while inspecting its temporary local database.
2. Choose **Inspect paired security and utility**. Compare benign task completion
   and task completion under attack. Refusal is not useful-task success.
3. Read each model's runtime tool guard section. Known policy decisions and full
   check coverage are separate facts. Benign interruption is a task-family rate;
   the general classifier false-positive rate stays unknown.
4. Load isolated tool receipts to inspect recorded SQL effects and reply release.
   A withheld reply cannot undo a known write. Cleanup must be confirmed.
5. Inspect **Runtime guardrails**. The sample policy is disabled after execution.
   Its saved approval and retained receipts do not authorize another run.
6. Stop the terminal with Ctrl+C to remove its temporary database.

The small sample intentionally cannot pass its population security gate. Native
SDK parsing, PostgreSQL, separate checker/broker processes, contained SQL and
cleanup are real; model behavior and usage are synthetic. There are zero paid
provider calls. See [the combined contract](../../docs/guarded-security.md) and
[the ordinary paired sample](../paired-security/README.md).
