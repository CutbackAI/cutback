# Cutback

Find where an AI application spends money. Test cheaper alternatives against its quality requirements. Review the evidence before changing anything.

**Start with a small application:** [the independent support desk](https://github.com/CutbackAI/support-desk-example) routes tickets with local model fixtures. Run it first, connect the metadata collector, and see what reaches the report. No provider account or paid inference is needed; it works without Cutback too.

Cutback is an Apache-2.0 Python tool with an offline scanner and a self-hosted collector, PostgreSQL worker and dashboard. **Pre-alpha: synthetic tests demonstrate the workflow; production behavior and real customer savings are not validated.**

[What works, which version to install, and what remains](https://github.com/CutbackAI/cutback/blob/main/docs/status.md). The GitHub source contains workflows that are not yet in the published PyPI package. The source-built demo also requires Git on PATH to prepare its source-bound retrieval application; the collector image includes a pinned Git package.

Source checkouts also include [agent and RAG evaluation](https://github.com/CutbackAI/cutback/blob/main/docs/agent-evaluation.md): native tool-call conversations, declared final-state checks, retrieval/citation evidence and individual durable request receipts. Try the [independent applications](https://github.com/CutbackAI/cutback/blob/main/examples/agent-evaluation/README.md), then inspect them in the guided demo. PyPI dev5 remains the earlier package.

Optional [semantic episode checks](https://github.com/CutbackAI/cutback/blob/main/docs/episode-semantic-judging.md) assess answers against declared references, actually returned documents and observed conversation constraints. The guided dashboard separates judge costs, exact-quote checks and unknown results. These are model assessments, not independent verification of customer quality.

The [isolated record sample](https://github.com/CutbackAI/cutback/blob/main/examples/isolated-tools/README.md) adds an actual read → SQL update → final-state check inside a contained tool process. Its model replies are scripted; its HTTP, SQL effects, durable receipts and cleanup are real. This source increment passed installed and hosted acceptance in PR 46 and is not in the PyPI dev5 package. See the [separate broker and consent contract](https://github.com/CutbackAI/cutback/blob/main/docs/isolated-tools.md).

The source [runtime guardrail application](https://github.com/CutbackAI/cutback/blob/main/examples/runtime-guardrails/README.md) demonstrates input denial before dispatch and output denial after a charged call. Try `cutback guardrails demo` locally, or `cutback demo --runtime-guardrails` for the native gateway/PostgreSQL/dashboard journey. This follow-up is under qualification; its literal/schema/tool-name rules do not establish semantic safety or real customer quality. [Scope and current gates](https://github.com/CutbackAI/cutback/blob/main/docs/runtime-guardrails.md).

The [guarded tool sample](https://github.com/CutbackAI/cutback/blob/main/examples/guarded-tools/README.md), under source qualification, connects the checker to actual contained record operations. It shows a write that completes while its response is withheld, with separate dispatch, effect and release evidence. It makes zero paid provider calls and does not run production application tools.

The source-based [semantic cache walkthrough](https://github.com/CutbackAI/cutback/blob/main/examples/semantic-cache/README.md) runs with `uv run cutback demo --semantic-cache`. Curate encrypted answers, inspect why a tempting wrong match fails, then review separate confirmation and activation. This supplied-vector feature passed source acceptance in PR 45; its synthetic controls establish workflow mechanics, while real embedding quality and savings remain unverified.

Source checkouts also include [context-compression comparisons](https://github.com/CutbackAI/cutback/blob/main/docs/context-compression.md): actual input transformations, answer-quality gates, native and declared CPU costs, untouched confirmation and a checked settings change with rollback. The [independent context application](https://github.com/CutbackAI/cutback/blob/main/examples/context-compression/README.md) and guided **Compression** page include an intentionally cheaper alternative that loses an essential fact. This is extractive compression; it does not claim semantic equivalence.

New source-based [automatic campaigns](https://github.com/CutbackAI/cutback/blob/main/docs/improvement-campaigns.md) prepare several model and output-limit alternatives under one reviewed budget. They compare those first, use reserved instruction search only when needed, then confirm one candidate before preparing application checks and a reversible source change. [Run the independent example](https://github.com/CutbackAI/cutback/blob/main/examples/automatic-portfolio/README.md); no provider key is needed for its synthetic journey.

From a matching source checkout, `uv run cutback demo --ticket-router` adds a three-label application to **Explore sample**. Inspect why a cheap constant answer fails, read authorized paired cases, and follow the automatically prepared change and rollback. The 240 synthetic rows stay in 60 declared intent families; they are not a real-model benchmark.

Source checkouts can also run `uv run cutback demo --provider-batch`: an independent three-category task with native asynchronous submission, per-item budget reservations, out-of-order result collection and provider-file cleanup. The [batch guide](https://github.com/CutbackAI/cutback/blob/main/docs/provider-batches.md) explains explicit storage approval, cancellation and uncertain billing. This source feature is under qualification and is absent from PyPI dev5; its model replies and usage are synthetic.

The source [paired security sample](https://github.com/CutbackAI/cutback/blob/main/examples/paired-security/README.md) shows why blocking an attack is insufficient when the agent also refuses legitimate work. Versioned benign/attack families, separate security and utility results, explicit unknowns and canonical tool receipts passed source acceptance in PR 47. The scripted sample intentionally cannot pass its small-sample security gate.

## Start with a working application

For the source-only cache eligibility workflow, see the [eight-call request-reuse example](https://github.com/CutbackAI/cutback/blob/main/examples/request-reuse/README.md). Its provider transport is synthetic; it distinguishes modeled potential reuse from measured delivery and real savings.

The support-desk sample routes tickets by category and priority. Two workflows, ticket triage and escalation, share one native SDK call. It shows why a cheaper configuration that mishandles urgent tickets must be rejected.

You need Python 3.11–3.13 and running Docker Desktop. Install in a fresh virtual environment. The dashboard is already built; no Node setup or provider API key is needed for the sample.

```sh
python -m pip install "cutback[server,openai]==0.1.0.dev5"
cutback demo
```

Wait for `Ready:` in the terminal, then click **Explore sample** in the browser.

| Configuration | Expected sample decision | Why |
|---|---|---|
| Baseline | Reference behavior | Classifies ticket category and priority |
| Compact candidate | Passes sample gates | Preserves synthetic labels with lower modeled fixture cost |
| Unsafe candidate | Not eligible | Loses urgent priorities; a required criterion fails |

**All provider responses in the demo are synthetic.** The scanner, native SDK observation, authenticated HTTP collector, PostgreSQL, encrypted evidence, approvals and worker are real. Fixture prices and latency are not real-model benchmarks, invoices or customer savings. No paid inference occurs.

The sample stays available for exploration while the command runs. Inspect calls, modeled usage, scorecards, failure-first paired cases, immutable label corrections, smoke/pilot/full receipts and a source diff with rollback. Costs distinguish application, evaluation, judge and unknown traffic. Encrypted diffs can be reviewed on the ordinary Changes page. Nothing is applied or deployed. Press Ctrl+C to remove its own temporary database and encrypted artifacts.

![Synthetic support-desk comparison: compact passes and unsafe fails priority](https://raw.githubusercontent.com/CutbackAI/cutback/v0.1.0.dev5/docs/assets/support-desk-decisions.png)

Read [the walkthrough and testing checklist](https://github.com/CutbackAI/cutback/blob/v0.1.0.dev5/docs/testing-the-sample.md) for every expected result and troubleshooting. This walkthrough targets dev5. Earlier releases have different evidence and review controls.

## Try the offline scanner without Docker

```sh
cutback sample support-desk
cutback scan support-desk
cutback opportunities --target support-desk
```

This copies a standalone native-SDK app and owned synthetic cases. Scanning parses files without executing them or making a network request. It finds source call sites; historical usage, spend and quality remain unknown until measured. Add `--json` for machine-readable scan/opportunity output.

## Let your coding agent do the setup

Ask: **Use Cutback to discover this application's AI workflows and examples, prepare instrumentation and a bounded source-bound evaluation, then complete every authorized step and present a tested diff with rollback.**

```sh
cutback optimize . --json
cutback workflow status
```

The saved job prepares source contracts, fixture candidates, instrumentation choices and the next decision. With permitted examples and an approved destination, `workflow prepare` handles scoped project/policy/dataset setup, preserves the application's native request, chooses qualified candidates and freezes one exact budget. Approval queues one run; subsequent continuations resume it. The dashboard's **Agent workflow** explains the path and shows registered contracts, plans and runs. [Walkthrough, scope and recovery](https://github.com/CutbackAI/cutback/blob/v0.1.0.dev5/docs/agent-workflow.md).

![Agent workflow with a synthetic source-bound local run](https://raw.githubusercontent.com/CutbackAI/cutback/v0.1.0.dev5/docs/assets/agent-workflow.png)

## Explore newer source workflows

The commands above target the published dev5 package. For the newer source-checkout workflows:

| Follow this journey | Start here |
|---|---|
| Fit input-size routes on training receipts, then validate and independently confirm | [Independent ticket application, source acceptance in progress](https://github.com/CutbackAI/cutback/blob/main/examples/learned-routing/README.md) |
| Prepare a source-bound routing change and check both application trees | [Independent application-agent example, under qualification](https://github.com/CutbackAI/cutback/blob/main/examples/source-routing/README.md) |
| Propose checks from training examples, review them, then validate and confirm | [Reviewed criteria sample, source qualification](https://github.com/CutbackAI/cutback/blob/main/examples/reviewed-criteria/README.md) |
| Generate and review development scenarios, then evaluate them separately | [Independent scenario sample, under qualification](https://github.com/CutbackAI/cutback/blob/main/examples/generated-scenarios/README.md) |
| Generate semantic episode rubrics, pin owner review and compare untouched groups | [Independent proxy sample, source under qualification](https://github.com/CutbackAI/cutback/blob/main/examples/semantic-rubrics/README.md) |
| Investigate a whole workflow or session | [Tracing walkthrough](https://github.com/CutbackAI/cutback/blob/main/docs/workflow-tracing.md) |
| Check whether a tool guard also stops legitimate work | [Guarded security/utility sample, source qualification](https://github.com/CutbackAI/cutback/blob/main/examples/guarded-security/README.md) |
| Account for failed tasks and optimization overhead | [Task-economics sample, source checkout](https://github.com/CutbackAI/cutback/blob/main/examples/task-economics/README.md) |
| Try a cheaper model/cap first, then generate instructions only if needed | [Runnable campaign, confirmation and rollback, source checkout](https://github.com/CutbackAI/cutback/blob/main/examples/improvement-campaign/README.md) |
| Optimize actual corpus chunking and retrieval context | [Independent RAG application and confirmation workflow](https://github.com/CutbackAI/cutback/blob/main/examples/retrieval-optimization/README.md) |
| Compare original and compressed context against answer-quality requirements | [Independent context application, native/CPU costs, confirmation and checked rollback](https://github.com/CutbackAI/cutback/blob/main/examples/context-compression/README.md) |
| Search model and context choices together, then review one combined source change | [Independent joint-search application and agent walkthrough](https://github.com/CutbackAI/cutback/blob/main/examples/model-context-search/README.md) |
| Connect a reviewed change to later task outcomes | [`--outcomes` sample and agent follow-up](https://github.com/CutbackAI/cutback/blob/main/docs/campaign-outcomes.md) |
| Prepare instruction search from a cheapest-model application | [`workflow prepare --for-campaign` and vendor scope](https://github.com/CutbackAI/cutback/blob/main/docs/improvement-campaigns.md) |
| Keep an existing OpenAI 3.35.1 application pin | [Separate locked SDK qualification and limits](https://github.com/CutbackAI/cutback/blob/main/infra/sdk-compat/README.md) |
| Understand modern cache usage and private contract rates | [Independent arithmetic and readable pricing tables](https://github.com/CutbackAI/cutback/blob/main/docs/modern-model-accounting.md) |
| Preserve a modern application's request during instruction search | [Explicit native text contracts and independent application](https://github.com/CutbackAI/cutback/blob/main/docs/native-modern-text.md) |
| Use native text streams and JSON under shared application allowances | [Native gateway sample, first-text timing and billing uncertainty](https://github.com/CutbackAI/cutback/blob/main/examples/gateway/README.md) |
| Measure an owned local ingest/query envelope | [Load profile and diagnostic limits, source checkout](https://github.com/CutbackAI/cutback/blob/main/docs/load-qualification.md) |
| Review failures and build a versioned regression dataset | [Import and review](https://github.com/CutbackAI/cutback/blob/main/docs/imports-and-review.md) |
| Version a prompt, compare it and roll back an alias | [Prompt sample](https://github.com/CutbackAI/cutback/blob/main/examples/prompt-releases/README.md) and [application checks](https://github.com/CutbackAI/cutback/blob/main/examples/application-checks/README.md) |
| Open and triage a technical incident | [Monitoring sample](https://github.com/CutbackAI/cutback/tree/main/examples/online-monitoring) |
| Catch an empty answer or evaluate an authorized captured output | [Quality sample, no provider key](https://github.com/CutbackAI/cutback/tree/main/examples/online-quality) and [bounded judging](https://github.com/CutbackAI/cutback/blob/main/docs/online-quality.md) |

These source additions are not in the published dev5 wheel. [Acceptance and remaining roadmap](https://github.com/CutbackAI/cutback/blob/main/docs/progress.md) distinguish shipped source, tested limits and unfinished work. Model-judge estimates do not prove task success; content use, native requests and external notifications need their own scoped authorization.

## Use your own application

Start with [application onboarding](https://github.com/CutbackAI/cutback/blob/v0.1.0.dev5/docs/application-onboarding.md). Keep your dependencies pinned and save findings in that application's ignored `.cutback/` directory. Prepare an instrumentation diff before authorizing its application or deployment.

For runtime observation, connect a scoped ingestion key to your own collector. For optimization, supply permitted examples and explicit quality criteria, review the complete budget, and approve one frozen plan. Metadata is the default; raw content requires a separate active rights policy and encrypted storage. No application name, source or report needs to be published to this repository.

## Define what good means

[Experiment files](https://github.com/CutbackAI/cutback/blob/v0.1.0.dev5/docs/experiment-files.md) describe named model/prompt variants, required criteria, optional rubric judging, budgets and staged execution. The dashboard exposes scorecards, segments, descriptive paired comparisons and audited encrypted case reads. Lower cost never overrides a required quality failure; synthetic results never establish customer quality.

## What this solves

Use Cutback when you want a reviewable answer to “Can this workflow use a cheaper configuration without failing its declared checks?” It links source discovery, observed usage, frozen quality comparisons and a patch with rollback. It complements your existing tracing system; it does not claim exhaustive discovery, production readiness or universal provider support.

## Explore further

- [Unreleased imports and blind review](https://github.com/CutbackAI/cutback/blob/main/docs/imports-and-review.md): qualified export preview, assigned reviewers, immutable votes/adjudication, new regression datasets and cross-version holdout protection. Follow the [independent synthetic walkthrough](https://github.com/CutbackAI/cutback/blob/main/examples/review-workflow/README.md) in a source checkout; PyPI dev5 does not contain these features.

- [Unreleased whole-workflow investigation](https://github.com/CutbackAI/cutback/blob/main/docs/workflow-tracing.md): generic spans, sessions, an error waterfall and externally asserted delayed results, with an independent no-inference sample. Use a source checkout; the published dev5 wheel does not contain it.

- [Product research and roadmap](https://github.com/CutbackAI/cutback/blob/main/docs/product-research.md): expanded market research, a [120-requirement baseline](https://github.com/CutbackAI/cutback/blob/main/docs/market-baseline.md), material missing workflows and the proposed reason to choose Cutback. The broader platform is not complete. [Unreleased optimization search](https://github.com/CutbackAI/cutback/blob/main/docs/optimization-search.md) adds model/output-limit portfolios, separate confirmation, trade-offs and an evidence gate in source checkouts.

- [Other examples](https://github.com/CutbackAI/cutback/blob/v0.1.0.dev5/examples/README.md): sync/async/streams, FastAPI, Django, Lambda, workers and Node.
- [Existing OpenTelemetry pipelines](https://github.com/CutbackAI/cutback/blob/v0.1.0.dev5/docs/otel-ingestion.md): reuse a filtered JSON metadata export without stacking SDK hooks.
- [Provider compatibility](https://github.com/CutbackAI/cutback/blob/v0.1.0.dev5/docs/providers.md): exact supported SDKs/models and limitations.
- [Evaluation](https://github.com/CutbackAI/cutback/blob/v0.1.0.dev5/docs/evaluation.md): frozen datasets, independent labels, uncertainty and approval semantics.
- [Deployment](https://github.com/CutbackAI/cutback/blob/v0.1.0.dev5/docs/deployment.md) and [privacy](https://github.com/CutbackAI/cutback/blob/v0.1.0.dev5/docs/security-and-privacy.md): customer-owned PostgreSQL, scoped credentials, retention and encrypted payloads.
- [Agent workflows](https://github.com/CutbackAI/cutback/blob/v0.1.0.dev5/docs/agent-integration.md): portable instructions for supported agent clients.
- [Architecture](https://github.com/CutbackAI/cutback/blob/v0.1.0.dev5/docs/architecture.md), [contributing](https://github.com/CutbackAI/cutback/blob/v0.1.0.dev5/CONTRIBUTING.md) and [implementation progress](https://github.com/CutbackAI/cutback/blob/v0.1.0.dev5/docs/progress.md): internals, executed acceptance and outstanding external gates.

Optional identity, billing, storage, scheduling, routing and training are documented separately. They are not prerequisites for understanding the sample. JavaScript/TypeScript scanning and native Node observation are optional; npm publication is separate. No managed cloud service is operated by this repository.

Finite follow-up policies can check declared outcomes after a reviewed change: [automatic outcome observation](https://github.com/CutbackAI/cutback/blob/main/docs/automatic-outcome-observation.md). The independent `examples/improvement-campaign/sample.py --outcomes --watch` journey includes native synthetic search, application checks and a deliberately regressing later window. Working-source qualification and package availability are recorded in [progress](https://github.com/CutbackAI/cutback/blob/main/docs/progress.md).
