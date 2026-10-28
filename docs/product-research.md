# Product research and build decision

27 October 2026. The expanded primary-source review covers **46 documented product/service families plus one gated research lead**, selected upstream implementation files, evaluation research and the actual Cutback code. This supersedes the earlier 14-product overview as the product-planning reference. It does not supersede the master architecture/privacy specification or turn planned features into implemented ones.

**Cutback is not yet ready to claim a complete, competitive evaluation and monitoring platform.** Its scanner/native SDK/collector/PostgreSQL/evaluation/change pipeline is working within documented bounds. Source now also includes scoped blind review, prompt versions and releases, quality incidents, synthetic tool/RAG execution, task-cost windows, bounded native improvement campaigns and application checks. Important gaps remain in general application/tool execution, semantic agent judging and generative simulation, broader optimizer/source adapters, application gateway policies and observed first-use/comparative acceptance. Previous engineering-release completion records describe their specific release scopes, not this broader market baseline.

The [implementation roadmap](implementation-roadmap.md) records the bounded implementations for Releases 1–6 and the owned-local ingestion/query qualification in Release 7. Each walkthrough states its narrower limits. General agent execution, all optimization strategies and the broad platform are still unfinished; a passing synthetic sample does not close those gaps.

## Read these first

| Document | What it provides |
|---|---|
| [Market baseline](market-baseline.md) | 120 checkable acceptance requirements, each marked bounded, partial, missing or external, with current code anchors |
| [Differentiation decision](differentiation.md) | Competitive counterexamples, three substantial bets and a task-matched protocol that can disprove the advantage hypothesis |
| [Implementation roadmap](implementation-roadmap.md) | Seven dependency-ordered releases with architecture, privacy, concurrency, browser and end-to-end acceptance gates |
| [Primary-source inventory](research/market-sources-2026-10-27.md) | Observation/eval platforms, specialists, gateways, security, cloud services, standards and papers; availability and edition limits |
| [Pinned source inspection](research/source-inspection-2026-10-27.md) | Narrow code-level findings from Opik, Langfuse, Phoenix and GEPA with exact snapshots and source hashes |

## What changed in the assessment

The expected baseline is a connected loop: instrument an existing application, investigate a whole session, select a failure, review/correct it, make it a versioned regression case, compare a change, enforce the result in the application's checks, then monitor later outcomes. Having APIs or modules for isolated parts of that loop does not complete the experience.

Several proposed distinctions are already documented elsewhere:

- [Opik Ollie](https://www.comet.com/docs/opik/ollie) connects a local project, inspects source, proposes approved edits, replays inputs and creates regression tests.
- [Langfuse Halo](https://langfuse.com/docs/api-and-data-platform/features/halo) uses shared project tools with external coding agents and supports approved actions/background work. Its [architecture](https://langfuse.com/blog/2026-10-21-building-the-langfuse-assistant) explains scoped tools and an optional separate sandbox.
- [Opik Optimizer](https://www.comet.com/docs/opik/development/optimization-runs/overview), [GEPA](https://github.com/gepa-ai/gepa) and [MLflow](https://mlflow.org/docs/latest/genai/prompt-registry/optimize-prompts) already cover automatic prompt/agent optimization.
- [Braintrust review](https://www.braintrust.dev/docs/annotate/human-review/manage-review-work) and [multi-reviewer behavior](https://www.braintrust.dev/docs/annotate/human-review/multiple-reviewers) show the baseline goes beyond a manual correction button.
- [OpenLIT](https://github.com/openlit/openlit) documents an open self-hosted trace/evaluate/guard/harness-improvement loop. Open source plus an agent interface is an occupied position.

The broader review also corrected current-state assumptions: [TensorZero's repository](https://github.com/tensorzero/tensorzero) is archived; reviewed Weave documentation points to its successor; cloud/evaluator simulation features have differing preview/edition statuses. Dependency and parity plans must use actual supported versions and licenses.

## The proposed reason to switch

**Less engineering effort per independently tested, economically useful improvement to an existing application.** Keep the customer's SDK, tracing/gateway stack and coding agent. Automate failure-to-regression coverage, search across eligible strategies under durable total budgets, run the application's own checks, prepare exact changes/rollback, and measure later cost per successful task including failed attempts and optimization overhead.

That is an outcome to prove, not an established moat. [Joint cost/accuracy optimization](https://arxiv.org/abs/2407.01502) is existing research, and close competitors already implement parts of this workflow. The comparison must include those alternatives, a simple cheaper-model baseline, failed installations, unsupported workflows, human interventions and unknown costs. Synthetic demonstrations are engineering evidence, not customer ROI.

## Engineering next

1. Whole-workflow spans, session/trace investigation and correctly targeted evaluation metadata.
2. Existing-evidence import, reviewer assignment/blind review/adjudication and versioned regression datasets.
3. Prompt registry/playground/release controls plus portable application-check and CI contracts.
4. Versioned production evaluation rules, alerts/incidents and monitored change outcomes.
5. Qualified RAG/tool/multi-turn execution and outcome criteria, with isolated synthetic tool examples.
6. Pluggable budgeted optimization, exact application changes and measured total task economics.
7. Broader interoperability/specialties, operations/scale and observed first-use/comparative acceptance.

This is implementation order, not a calendar estimate or a completed release claim. Existing bounded search/confirmation is documented in [optimization search](optimization-search.md), with [acceptance evidence](evidence/search-acceptance.json). It does not implement the rest of this plan. PyPI `0.1.0.dev5` and its immutable release tag remain separate from unreleased source work.

No owner input is needed to build and test the authorized independent synthetic foundations. Real customer data/vendors/budgets, outreach, production changes and public customer claims require their relevant authorization and evidence. Prepare the engineering result before requesting those external decisions.
