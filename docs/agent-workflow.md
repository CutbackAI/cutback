# Optimize an application with your coding agent

The coding agent prepares the technical work. You decide data rights, business quality, destinations and paid budgets. Cutback keeps a resumable metadata journal in the application's ignored `.cutback/agent/` directory; optional credentials are separate private files. A corporate account is unnecessary.

Ask your agent:

> Use Cutback to optimize this application. Discover its AI workflows and existing examples, prepare instrumentation and an exact source-bound evaluation plan, then carry out every authorized step. Ask me only for missing rights, business criteria, destination or paid budget. Preserve my dependencies. Present a tested diff and rollback before deployment.

## First checkpoint: entirely offline

Use an isolated environment with Cutback installed. Run:

```sh
cutback optimize . --json
cutback workflow status
```

The scanner parses source without importing the application. The job discovers bounded root/nested JSON, JSONL, NDJSON and CSV fixture candidates, respecting ignores and secret-directory exclusions; discovery reads metadata, not fixture payloads. It prepares instrumentation choices for detected entry points, source dependencies, native request contracts, candidate strategy assessments and a typed next action. Source-only volume, spend and quality stay unknown. Repeating analysis resumes the same unchanged job; a changed source/fixture identity invalidates its plan authority.

Inspect the selected startup and use `cutback instrument --plan --target . --entrypoint app.py --json`. The shared planner preserves module docstrings/future imports, prepares exact attribution and bounded process shutdown, and supports an explicit inspected serverless handler. Applying instrumentation changes source: repeat `optimize` before freezing evaluation evidence. The coding agent runs your permitted application checks and preserves existing tracing wrappers and dependency pins.

## Prepare an approved destination and examples

For an approved local development destination, install `cutback[server,openai]` in an isolated environment and start Docker Desktop:

```sh
cutback collector up --target . --port 8080
cutback collector status --target .
```

This runs the installed collector/dashboard against its own labelled Docker PostgreSQL container and persistent volume. It binds only loopback and defaults to metadata. `collector down --target .` preserves the database and encrypted objects. Restarting restores them. Config, encryption keys and credentials stay in private ignored files, outside process arguments and reports. Protect the workspace with your operating system's account/ACL controls. This local service is development infrastructure; it does not deploy to a remote environment or provision a paid service.

After the owner authorizes selected example files **and selected workflow request content** for encrypted preparation, stop the local collector and restart with `--allow-payloads`. This enables the store; the following command creates a one-day scoped rights policy, imports only the specified examples, freezes splits and source/request hashes, chooses a qualified cheaper same-provider candidate and submits one plan. It makes **zero inference calls**:

```sh
cutback collector down --target .
cutback collector up --target . --port 8080 --allow-payloads
cutback workflow prepare --local --example cases.jsonl \
  --rights-basis synthetic --rights-reference owned-synthetic-review \
  --max-usd 1 --max-requests 100
```

These are synthetic-example rights, not authorization to import customer data. For permitted application records use the actual rights basis/reference. Repeat `--example` for up to 20 files. If several qualified workflows exist, the agent chooses one from `optimize` output and passes `--workflow ID`. `--mapping mapping.json` accepts a bounded mapping such as `{"input":"request.text","gold":"expected.category","group":"customer_group"}`; the mapping stays local. Existing output fields become references, never invented gold. `--independent-labels --minimum 0.9` is appropriate only when the owner has independently labelled examples and actually approves that business threshold. Structured gold prepares per-field required criteria automatically. Missing business criteria produce a technical-only plan with semantic quality **NOT VERIFIED**. Duplicate/customer groups remain together across frozen splits.

For an existing approved HTTPS collector, use `--endpoint URL --project UUID` with the scoped `CUTBACK_CONTROL_TOKEN` in the process environment instead of `--local`. That destination is saved; subsequent commands reuse it. Local preparation creates project, expiring project-admin and ingest credentials automatically. The agent uses the ingest credential only in the application's runtime secret configuration, never in reports or committed code. No UUID copying or handwritten submission YAML is needed for the local flow.

## Approve once, resume the same bounded run

Review the returned plan hash, qualified vendor/models, frozen source and dataset, quality scope, modeled worst-case amount, request count and ceiling. A zero/insufficient budget cannot authorize execution. A frozen contract cannot silently change.

```sh
cutback workflow continue --plan-hash EXACT_HASH --approval-reference actual-budget-review
```

This consumes one approval and queues one run. A configured remote worker processes it. For the owned local stack, an experiment-specific provider key belongs in the worker environment and the authorized agent explicitly runs `cutback workflow work`. The worker selects only this session's run, reserves its worst-case cost durably **before each native call**, shares the ceiling across all candidates and retains uncertain reservations. The command uses real providers when configured: run it only within a legitimate paid approval. Tests use synthetic HTTP transports; no demo key authorizes real requests.

```sh
cutback workflow continue
cutback workflow proposal --purpose-reference actual-evidence-review
```

Subsequent continuations poll the same run. A lost queue response reconciles by immutable plan/run records; an uncertain approval without a recorded run requires operator reconciliation and never triggers a second POST. A crashed billable operation is never automatically retried. Interrupted nonbillable preparation leaves its known policy/dataset references and `PREPARING` checkpoint for inspection rather than silently duplicating resources.

The proposal reads authorized encrypted plan evidence, regenerates the source binding and emits a concrete diff, rollback, integrity/syntax checks and staging steps. It remains **human review required**; it neither accepts a recommendation nor applies/uploads/deploys the patch. The agent inspects/runs the application's authorized tests, reviews every affected workflow (including shared config consumers), then presents that evidence. Apply/staging/canary decisions remain separately scoped. Source changes, different instructions/output caps/options, incompatible request surfaces and old/unbound plans cannot authorize a model-only patch. The ordinary recommendation review and encrypted patch upload controls remain available after explicit acceptance.

## Qualified scope and tests

Native replay currently qualifies bounded text-only OpenAI Responses/Chat and Anthropic Messages on the documented pinned SDKs. It preserves resolvable static instructions, simple parameter/f-string/message templates, structured output options, output cap and bound source/config/prompt dependencies. Streams, tools, multimodal input, dynamic mutations, custom provider destinations and unresolvable templates abstain for inspected adapter work. Current automatic candidate generation uses the existing qualified same-provider model pairs. A workflow already using the cheapest qualified model needs a reviewed prompt/config strategy; a strategy assessment is not a completed optimization. Arbitrary framework startup, business thresholds, production behavior and every third-party agent client's discovery remain unqualified until tested.

`tests/integration/test_agent_workflow.py` runs three independently created applications through ordinary analysis/preparation/approval, real PostgreSQL, native SDK synthetic transport, durable worker, evidence and source/config diff. It does not seed the support-desk demo. Other regressions cover payload-free offline resume, nested mappings, source changes, lost queue responses, tenant/RBAC and encrypted content revocation. `CUTBACK_TEST_LOCAL_STACK=true` enables the owned Docker persistence/ownership lifecycle test. See [implementation progress](progress.md) for executed evidence.
