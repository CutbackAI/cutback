# Cutback

Find where an AI application spends money. Test cheaper alternatives against its quality requirements. Review the evidence before changing anything.

Cutback is an Apache-2.0 Python tool with an offline scanner and a self-hosted collector, PostgreSQL worker and dashboard. **Pre-alpha: synthetic tests demonstrate the workflow; production behavior and real customer savings are not validated.**

## Start with a working application

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
