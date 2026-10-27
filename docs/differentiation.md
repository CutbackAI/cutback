# The reason to choose Cutback still has to be earned

Decision date: 27 October 2026. This is a product hypothesis and evaluation plan, not a claim of superiority, traction or fundraising readiness. The [market review](research/market-sources-2026-10-27.md) and [120-requirement audit](market-baseline.md) replace the earlier assumption that an agent plus cost charts would be distinctive.

## What the research rules out

The following are necessary capabilities or existing competitive categories. None is a defensible uniqueness claim on its own:

| Proposed distinction | Closest documented counterexample | Decision |
|---|---|---|
| “An agent investigates a failure and fixes your code” | [Opik Ollie](https://www.comet.com/docs/opik/ollie): local project bridge, source inspection, approved edits, replay and regression suite | Retire as a uniqueness claim. Compare the actual complete workflow. |
| “Automatic prompt/agent optimization” | [Opik Optimizer](https://www.comet.com/docs/opik/development/optimization-runs/overview), [GEPA](https://github.com/gepa-ai/gepa), [MLflow optimization](https://mlflow.org/docs/latest/genai/prompt-registry/optimize-prompts) | Baseline. Use qualified algorithms rather than rename an existing one. |
| “Open source, local, model/framework independent” | [OpenLIT](https://github.com/openlit/openlit), [OpenLLMetry](https://github.com/traceloop/openllmetry), [Giskard OSS](https://docs.giskard.ai/oss) | Architecture and distribution advantages are possible, but this positioning is occupied. |
| “Quality, cost and latency in one dashboard” | [Maxim](https://www.getmaxim.ai/docs/introduction/overview), [HoneyHive](https://docs.honeyhive.ai/v2/introduction/what-is-hhai), [LangSmith](https://www.langchain.com/langsmith/observability) | Baseline. A Pareto chart is descriptive evidence, not a business advantage. |
| “Evaluate full agents and multi-turn conversations” | [DeepEval](https://github.com/confident-ai/deepeval/blob/main/docs/content/docs/evaluation-end-to-end-multi-turn.mdx), [Ragas](https://docs.ragas.io/en/latest/concepts/metrics/available_metrics/), [Coval](https://docs.coval.ai/welcome) | Required for agent claims. Cutback currently lacks much of this workflow. |
| “Let an assistant generate tests or improve judges” | [Braintrust Loop](https://www.braintrust.dev/blog/loop), [Opik judge alignment](https://www.comet.com/docs/opik/evaluation/judge_alignment), [LangWatch](https://langwatch.ai/docs/introduction) | Baseline/expansion. Dataset leakage and calibration must be tested, not assumed solved. |
| “A background agent works through the same tools as your coding agent” | [Langfuse Halo](https://langfuse.com/docs/api-and-data-platform/features/halo), [architecture](https://langfuse.com/blog/2026-10-21-building-the-langfuse-assistant) | Documented overlap includes scoped MCP tools, approvals, background workers and a separate optional sandbox. Neither durable-looking UI progress nor an MCP wrapper establishes differentiation. |
| “An autonomous loop connects production failures, candidate PRs, evals and later outcomes” | [Replit's engineering account](https://replit.com/blog/evaluating-and-improving-agent-at-scale), [Cursor's release/evaluation tools](https://cursor.com/en-US/changelog) | This is also an established internal/built-in pattern. A reusable independent product may still help teams, but it must beat their assembly/maintenance effort. |
| “Secure and private” | [Invariant](https://github.com/invariantlabs-ai/invariant), [NeMo Guardrails](https://docs.nvidia.com/nemo/guardrails/about-nemo-guardrails-library/overview), [Promptfoo](https://www.promptfoo.dev/docs/red-team/) | An operating requirement, not proof that an application is safe. Customer-owned storage and metadata defaults remain mandatory. |

Absence of a feature from a competitor's reviewed page does not establish absence from its product. A broad claim such as “the only platform that closes the loop” is unsupported.

## Proposed product: an improvement workflow for an existing application

An engineer should be able to ask their existing coding agent:

> Find the expensive or failing workflows, create the missing regression cases from data I permit, try improvements within this budget, and give me the best independently tested change to review.

Cutback should do the discovery, evidence preparation, bounded experiments, application checks, source changes and outcome tracking. The engineer supplies the decisions the system cannot infer: rights to data, what a successful task means, allowed vendors/tools, maximum spend, and whether to change production. These decisions can be reusable policies; they should not require repeated manual navigation or hand-assembled JSON for routine work.

The **candidate advantage** is less engineering effort per safe, economically useful application improvement while retaining the existing SDK, gateway, observability stack and coding agent. Source fidelity, durable budgets, independent evidence and rollback are mechanisms supporting that outcome. Their combination has not yet been shown to outperform competitors.

The first proposed customer is a team with a meaningful recurring inference bill or costly quality failures in an existing text/structured/agent workflow, rights-cleared examples and a way to determine task success. This is a testable target, not validated demand. General consumer chat, every voice stack and every autonomous runtime are not one initial acceptance domain.

Built-in agent/application evals and an internal harness are alternatives too. Import or call their existing checks and preserve the team's investment. If Cutback merely adds a second dashboard and another dataset to maintain, it has increased the work instead of delivering the proposed benefit.

## Three substantial bets to build and test

### 1. Turn production failures into maintained application regression coverage

Observe existing traces or import a qualified export. Identify repeated failure patterns without uploading source or payloads by default. Ask for permitted example content only when needed. Propose a small, diverse review queue, explain uncovered task slices, collect independent corrections, and create a versioned regression contract. The agent should wire that contract into the application's own test process and retain provenance from failure to test to change.

The difficult engineering work is stable workflow identity, deduplication across datasets, meaningful task criteria, tool-state assertions, real application checks and source impact analysis. Automatically generating a hundred similar synthetic prompts is not the acceptance criterion. Generated cases supplement independent evidence; they cannot replace it.

**Test:** compare time and interventions required to convert the same seeded failures into useful, non-leaking regression tests in Cutback, the team's current process, and Opik/another relevant incumbent. A blind domain reviewer assesses whether the tests catch the known failure and reject an unrelated bad fix. Record missed and redundant cases. Retire the advantage claim if the workflow is no easier or the tests are less useful.

### 2. Optimize cost per successful task, including the cost of optimization

Choose model, prompt, output bound, cache/routing/retrieval strategy or training only when the workload and adapters support it. Compare the whole task, including retries, tools, retrieval, rejected outputs and judge/optimizer calls. Show whether a cheaper model causes extra turns or failures. State which cost components are known, estimated or unavailable.

For a measured window with complete task-cost allocation and independent outcomes:

`cost per successful task = total measured cost of all attempted tasks / independently successful tasks`

Failed attempts belong in the numerator. Unknown outcomes are not successes. If cost allocation or outcomes are incomplete, publish the known subtotal and denominators and keep the full metric unknown. If there are no successes, the metric is undefined; do not show zero. Overlapping span durations must not be summed into end-to-end latency. Human/infrastructure costs require declared measurements or assumptions. [Joint cost/accuracy optimization](https://arxiv.org/abs/2407.01502) is established research; the potential advantage must be the delivered workflow and measured result, not the metric's existence.

Report one-time search/confirmation/review overhead separately from recurring task cost, then calculate payback only under an explicit traffic/cost scenario. Do not extrapolate a tiny synthetic token reduction into annual customer savings. Once released, link each observation window to the exact application/model/prompt version and retain a rollback explanation.

**Test:** include a deliberately cheap model that fails more often, a tool-heavy strategy, an unpriced operation, missing outcome feedback, an apparent latency win caused by missing spans, and a strategy whose search cost exceeds its expected benefit. A useful system rejects misleading wins and shows when doing nothing is best.

### 3. Make optimization an auditable, interruption-safe engineering operation

Use one durable, approved envelope for proposer, baseline, candidate, judge, simulator and confirmation calls. Freeze input access, metrics, data groups and candidate versions at the relevant checkpoints. Optimizers receive only the permitted training/validation views. A selected winner receives an independent confirmation plan; an uncertain billable crash cannot restart automatically.

The existing ledger/source binding is a useful starting point, not a complete multi-optimizer control plane. Static inspection of [GEPA's budget tracker](https://github.com/gepa-ai/gepa/blob/d7918ba2f382d5d0652c10814bb278c1c7ffa7c3/src/gepa/oa/budget.py) shows separate in-process evaluation-call and engine-owned proposer budgets. That specific component is not a PostgreSQL reservation service. This is a concrete integration requirement, **not evidence that GEPA or competing platforms have no budgets**.

Package the result so a reviewer can see the exact change, native request fidelity, all evaluated/affected workflows, rejected strategies, heldout status, costs, reproducible checks and rollback. A different CI runner must be able to validate the artifact without trusting a mutable local journal. Evidence must bind the base source and proposed source separately; checking only unchanged pre-patch source is insufficient.

**Test:** disconnect the worker before/after the call boundary, race workers and confirmations, mutate the source/config/dataset, attempt cross-tenant artifact use, reimport an exposed test group, and hide a failed trial. Every case must either preserve exact evidence or stop with an actionable reason. A “resume” button is not the objective if resuming could duplicate a charge.

## What might become defensible

Reliable source adapters and impact analysis are costly to maintain across real applications. High-quality application regression contracts and measured accepted/rejected change outcomes could make the product increasingly useful. Distribution through existing coding agents could reduce adoption effort. None of these is automatically a moat: competitors can build them, model improvements can commoditize parts of the work, and integration maintenance can consume the benefit.

Any learning from customer data must be explicitly permitted. No cross-customer payload collection, training corpus, proprietary benchmark, data network or customer logo is assumed. Private application integrations remain outside this public repository.

## Comparison protocol before a differentiation claim

1. Freeze a rights-cleared task suite and failure taxonomy before tuning. Include structured extraction/classification, a RAG task and a stateful synthetic tool workflow; start with only the adapters actually implemented.
2. Separate optimizer training/validation, evaluator calibration and final confirmation groups. Track normalized group identities across dataset versions/imports. An owner able to read test data still prevents a universal claim that it was never seen.
3. Use the same source state, allowed changes, model/provider choices, dollar/request/time ceilings and task-quality constraints for Cutback and comparison workflows. Report tooling setup effort separately and amortized over repeated use.
4. Compare against the team's incumbent process, a simple “use the cheapest eligible model” baseline, a qualified optimizer alone, and a close complete workflow such as Opik. Record exact versions/editions and unavailable capabilities rather than silently substituting an easier opponent.
5. Record installation success, time to first useful reviewed change, maintainer interventions, unsupported-workflow rate, test coverage, critical regressions, independent task success, p95 latency where raw samples permit it, complete costs, search overhead, review effort and rollback/reproduction success.
6. Include failed installations, abstentions and rejected changes in the denominator. Blind task-quality review where feasible; retain disagreements and invalid judge results. Do not compare a human-gold metric on one side to a judge score on the other.
7. Predeclare a practically meaningful improvement with the participating team before running. For planning only, a possible target is materially less engineer time with non-inferior task success and lower fully accounted task cost. Exact thresholds and sample sizes depend on that team's cost/risk; no numerical success result is claimed here.
8. Repeat on independent applications and later traffic windows. If the claimed advantage disappears against the close alternative or requires substantial bespoke maintainer help, change the product hypothesis before expanding the launch story.

Paid providers, customer accounts/data, outreach and production experiments require the relevant owner authorization. Engineering can build and test the synthetic comparison harness, public independent examples and failure recovery before asking for those decisions.

## Launch and company evidence are different gates

A broad OSS launch needs the working baseline journeys, independent examples, install/recovery instructions and observed first-use success. A strong company case additionally needs a recurring expensive problem, repeated successful use, a reason to switch, a buyer and willingness to pay. Stars and fundraising cannot be manufactured by a feature checklist.
