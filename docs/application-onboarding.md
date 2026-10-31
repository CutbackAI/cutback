# Use Cutback in an independent application

Cutback is ready for an agent to start with a supported Python application: local inventory, source-linked cost candidates and a reviewable instrumentation plan need no private reference application, hosted account or paid inference. Runtime observation requires a configured collector; semantic quality and savings require authorized evidence. See [provider support](providers.md) before enabling observation.

For automatic source/fixture preparation, persistent local services, exact plan approval and resumable runs, follow [the agent workflow](agent-workflow.md). The application-specific journal stays private; a corporate account is unnecessary.

Open the application repository in your coding agent and give it this request:

> Use Cutback to inventory this repository's AI calls and identify cost-saving candidates. Install Cutback in an isolated tool environment if needed. Read the application's instructions and preserve its existing SDK/dependency pins. Start offline, keep raw-content capture off, and save reports only in this application's ignored `.cutback/` directory. Inspect the actual startup and handler paths, then prepare a minimal instrumentation diff and rollback without applying or deploying it. Explain what is discovered, observed, modeled or unknown. Ask for a concrete approved destination, permitted data/vendors and a bounded budget only when the next step needs them. Never publish application names, source, reports, secrets or deployment details to the Cutback repository or its issues/releases.

The agent can use `uv tool install cutback==0.1.0.dev5` or a separate virtual environment. Installing the analysis tool does not require changing the application's lockfile. A checked-out Cutback repository is optional for analysis; its [skills](agent-integration.md) provide the longer workflow when installed in the selected agent client.

From the application root, the first completed checkpoint is:

```sh
cutback doctor --json
cutback optimize . --json
cutback workflow status
cutback instrument --target . --entrypoint actual/entrypoint.py --plan --json
```

Working-source follow-up under qualification: run
`cutback workflow explain --target .` for a readable explanation of the saved
discovery, or add `--json` for its bounded metadata report. It identifies missing
request/output bounds, unresolved model references, expanded arguments, dynamic
preprocessing, unsupported options, custom destinations and storage-policy
differences, without showing their values. A call that needs an adapter does not
become replayable by suppressing its diagnostic. Preserve required application
behavior and qualify the adapter, then rerun `cutback optimize . --json`.
The explanation reads the saved snapshot; it does not recheck current files,
connect to a collector, call a provider or edit application code. PyPI dev5 does
not contain this new command.

Inspect startup before choosing the entrypoint. Scanning and fixture discovery parse/index files without importing application code, reading `.env` values, uploading payloads or performing inference. Ignore `.cutback/` before saving reports because inventory may identify application files and workflows. Run a full source review before sharing any resulting artifact externally.

The checkpoint is complete when every detected call has a source reference, unsupported/dynamic calls are explicit, spend/volume/quality remain unknown where no runtime evidence exists, and the instrumentation proposal leaves application files unchanged. Existing provider pins stay in place; install a compatible instrumentation extra only as part of the reviewed integration. Unsupported SDKs require adapter work rather than a silent upgrade.

For runtime observation, the agent prepares a separate Cutback deployment from the [deployment guide](deployment.md), using customer-owned PostgreSQL, HTTPS and scoped ingestion/control keys. The application receives only its ingestion credential through runtime secrets. Telemetry stays disabled until that configuration is authorized. Serverless integrations include a bounded flush and must preserve native results/errors during an outage. Apply/deploy approval is distinct from the source-only first checkpoint.

For optimization, use `workflow prepare` to preserve the qualified native request and source/config/prompt binding. Standalone unbound plans cannot authorize automatic source patches. The agent discovers existing permitted fixtures first. It freezes rights, dataset/splits, metric, candidate compatibility and worst-case request/USD bounds before requesting an experiment approval. A successful technical check, an LLM preference or a training loss is not independent semantic validation. Changes require a source-bound diff, evidence and rollback; traffic changes remain separately approved.

All public [examples](../examples/README.md) are generic synthetic applications. `scripts/wheel_smoke.py` installs the core wheel in a fresh environment and executes the first checkpoint against a new external synthetic repository, without provider/server dependencies or edits to its source. Actual skill discovery and tool permissions still depend on the selected agent client; they are not represented as tested in every client.

## Verify source attribution and review changes

The instrumentation plan includes `.cutback/source-map.json`. It verifies source hashes at process startup and joins exact SDK frames to scanner call-site IDs and upstream entry functions. Two workflows sharing one SDK helper retain distinct workflow IDs. A changed file is not attributed using stale metadata. Regenerate the map and restart after edits; hot reload, dynamically generated code and unobserved traffic remain explicit coverage limits. Named `workflow(...)` scopes can override workflow identity while retaining mapped call sites. `--handler your_handler` adds a bounded serverless drain capped by the remaining invocation time when available.

After uploading inventory and observing an approved request, open Inventory for source-linked application counts and modeled subtotals. In Costs, select application, evaluation, judge or unknown traffic before comparing totals. Legacy events/archives have unknown purpose rather than inferred application traffic.

Name experiments in the dashboard or draft API. Required scorecard criteria enforce their own sample counts, independent groups and regression bounds. The primary decision distinguishes technical checks, reference similarity, judge preference and independent held-out labels. Review failures in paired case view before accepting a recommendation. A correction creates a new dataset, invalidates representativeness assumptions, and requires a new experiment approval; it never edits an old run.

Generate a local patch using `cutback recommendations patch --help` and the source hash you reviewed. Default output stays local. To explicitly authorize encrypted upload, also supply `--upload-project PROJECT_UUID --approval-ref REVIEW_REFERENCE` with a scoped `CUTBACK_ADMIN_TOKEN`. The accepted recommendation, frozen dataset and plan must match, and an active content policy must permit the source diff. Source redaction that would change patch semantics is rejected. The collector records the CLI's source attestation; it cannot independently inspect your checkout. Read the diff and rollback under an audited purpose in Changes. Applying it, deploying staging and changing production traffic remain separate reviewed actions.
