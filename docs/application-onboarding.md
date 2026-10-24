# Use Cutback in an independent application

Cutback is ready for an agent to start with a supported Python application: local inventory, source-linked cost candidates and a reviewable instrumentation plan need no private reference application, hosted account or paid inference. Runtime observation requires a configured collector; semantic quality and savings require authorized evidence. See [provider support](providers.md) before enabling observation.

Open the application repository in your coding agent and give it this request:

> Use Cutback to inventory this repository's AI calls and identify cost-saving candidates. Install Cutback in an isolated tool environment if needed. Read the application's instructions and preserve its existing SDK/dependency pins. Start offline, keep raw-content capture off, and save reports only in this application's ignored `.cutback/` directory. Inspect the actual startup and handler paths, then prepare a minimal instrumentation diff and rollback without applying or deploying it. Explain what is discovered, observed, modeled or unknown. Ask for a concrete approved destination, permitted data/vendors and a bounded budget only when the next step needs them. Never publish application names, source, reports, secrets or deployment details to the Cutback repository or its issues/releases.

The agent can use `uv tool install cutback==0.1.0.dev2` or a separate virtual environment. Installing the analysis tool does not require changing the application's lockfile. A checked-out Cutback repository is optional for analysis; its [skills](agent-integration.md) provide the longer workflow when installed in the selected agent client.

From the application root, the first completed checkpoint is:

```sh
cutback doctor --json
cutback scan . --json --output .cutback/scan.json
cutback opportunities --target .
cutback datasets discover --target .
cutback instrument --target . --entrypoint actual/entrypoint.py --plan --json
```

Inspect startup before choosing the entrypoint. Scanning and fixture discovery parse/index files without importing application code, reading `.env` values, uploading payloads or performing inference. Ignore `.cutback/` before saving reports because inventory may identify application files and workflows. Run a full source review before sharing any resulting artifact externally.

The checkpoint is complete when every detected call has a source reference, unsupported/dynamic calls are explicit, spend/volume/quality remain unknown where no runtime evidence exists, and the instrumentation proposal leaves application files unchanged. Existing provider pins stay in place; install a compatible instrumentation extra only as part of the reviewed integration. Unsupported SDKs require adapter work rather than a silent upgrade.

For runtime observation, the agent prepares a separate Cutback deployment from the [deployment guide](deployment.md), using customer-owned PostgreSQL, HTTPS and scoped ingestion/control keys. The application receives only its ingestion credential through runtime secrets. Telemetry stays disabled until that configuration is authorized. Serverless integrations include a bounded flush and must preserve native results/errors during an outage. Apply/deploy approval is distinct from the source-only first checkpoint.

For optimization, the agent discovers existing permitted fixtures first. It freezes rights, dataset/splits, metric, candidate compatibility and worst-case request/USD bounds before requesting an experiment approval. A successful technical check, an LLM preference or a training loss is not independent semantic validation. Changes require a source-bound diff, evidence and rollback; traffic changes remain separately approved.

All public [examples](../examples/README.md) are generic synthetic applications. `scripts/wheel_smoke.py` installs the core wheel in a fresh environment and executes the first checkpoint against a new external synthetic repository, without provider/server dependencies or edits to its source. Actual skill discovery and tool permissions still depend on the selected agent client; they are not represented as tested in every client.
