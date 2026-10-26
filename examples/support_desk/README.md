# Support-desk reference application

Run `uv run cutback demo` from the repository root for the complete guided sample. Start Docker Desktop first. The launcher uses actual PostgreSQL, HTTP collection, SDK observation, encrypted evidence, one-run approvals and the durable worker. All inference is intercepted by synthetic transports; no API key is needed.

To obtain an independent source project, run `uv run cutback sample support-desk`. It exports `app.py`, synthetic `cases.jsonl` and safe ignore rules. Its native SDK application code is maintained in `src/cutback/sample_app/app.py` and ships in the wheel. It has no Cutback import. Scan it with `cutback scan support-desk`; scanning never executes it.

The task is to classify each ticket's category and priority. The compact variant must preserve both labels. The unsafe variant deliberately loses urgent priorities, demonstrating why lower modeled cost cannot override quality gates. These are controlled fixtures, not claims about the named models' real performance.

Follow [the step-by-step test guide](../../docs/testing-the-sample.md) for expected decisions, encrypted case inspection, stage receipts, source diff/rollback and cleanup. See [experiment files](../../docs/experiment-files.md) to adapt the configuration using your own authorized evidence.
