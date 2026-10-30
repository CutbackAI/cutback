# Resist an attack and still finish the task

Compare two scripted controls on paired benign/adversarial record tasks. Both preserve the tool boundary. One refuses useful work when untrusted content looks hostile; the other resolves the authorized record. Native SDK conversations, the separate broker, isolated SQL, PostgreSQL receipts and dashboard are real. Model behavior and usage are synthetic, not evidence of actual model security or customer savings.

Use Python 3.11–3.13, Git, Docker Desktop with Linux containers and this source checkout. No provider key is required. From the repository root:

```sh
uv sync --locked --all-extras --dev
docker build --file infra/compose/Dockerfile --tag cutback-security-local .
docker image inspect cutback-security-local --format "{{.Id}}"
```

Copy the complete `sha256:` ID from the last command below; a tag is intentionally insufficient:

```sh
uv run cutback demo --security-suite --tool-image-id sha256:YOUR_64_HEX_IMAGE_ID
```

The launcher owns a temporary local database, collector, encrypted store and separate tool broker. It makes no paid provider requests and modifies no production application. Ctrl+C stops the sample and removes its owned database/tool processes.

1. Open the displayed dashboard and choose **Explore sample**.
2. Find **Resist an attack and still finish the task** and select **Inspect paired security and utility**.
3. Compare **refusal-control** and **scoped-task-control**. Both have zero realized attacks in this tiny scripted sample. The first has zero attacked-task utility; the second completes it. Both complete benign work.
4. Confirm **insufficient evidence**. One family cannot establish population security. Inspect intervals, known/unknown families, safe-but-useless counts and unknown general FPR/complete-task cost.
5. Load execution and isolated-tool receipts. Inspect reserved native calls, scoped operations and confirmed cleanup. Ordinary reports contain no raw canaries or tool payloads.
6. In **Experiments → Plan an experiment**, choose the paired dataset. Review security/utility thresholds and the operator's tool scope. Freezing makes no requests; real-provider work needs separate budget/content approval.

The independent implementation lives in `cutback.sample_app.security_suite`, `cutback.sample_app.isolated_records` and `cutback.demo_security_suites`. [Prepare your own authorized suite](../../docs/security-suites.md). No private application is required.
