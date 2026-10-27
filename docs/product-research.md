# Product research and implementation priorities

Research date: 27 October 2026. This is a primary-source desk review and a code audit, not customer discovery, a hands-on competitor benchmark, or proof of product-market fit. Vendor documentation establishes advertised capabilities, not their performance. Features not mentioned in these sources must not be described as absent.

## Finding

Cutback has a substantial technical foundation, but its useful automation is narrower than its vision. A dashboard, evaluations, model routing, and an agent interface are established capabilities in this market. Adding their names to a feature list will not create a compelling reason to switch.

The product hypothesis to test is: **ask your coding agent to reduce the cost of an existing application, and receive a source-bound, quality-tested change with an explanation and rollback, while keeping your existing SDK and infrastructure.** The outcome is a reviewable application improvement. The hypothesis is not a claim that competitors cannot do this or that customers will pay for it.

## Competitive evidence

| Product | Relevant documented capabilities | Consequence for Cutback |
|---|---|---|
| [Langfuse](https://langfuse.com/docs/evaluation/experiments/experiments-via-ui) | Dataset versions, prompt/model experiments, structured outputs, code/judge evaluations, comparisons | Experiments and prompt versioning are baseline expectations. Integrating existing evidence matters more than asking teams to recreate it. |
| [LangSmith](https://www.langchain.com/langsmith/evaluation) | Offline/online evaluation, human annotation, judge calibration, trajectory evaluation, CI thresholds | Single-call synthetic quality checks are insufficient for a general agent optimization claim. Full application outcomes and human review need first-class support. |
| [Braintrust Loop](https://www.braintrust.dev/blog/loop) | Natural-language trace investigation, dataset/scorer generation, experiment analysis and prompt improvements | A conversational assistant is not itself a differentiator. Make the result reproducible and actionable in source. |
| [Arize Phoenix](https://arize.com/docs/phoenix) | OpenTelemetry/OpenInference tracing, evaluations, human annotations, prompt management/replay, datasets/experiments and coding-agent setup | Open source and agent onboarding are also established. Work alongside existing observability rather than claiming to replace its breadth. |
| [Helicone](https://www.helicone.ai/pricing) | Sessions, user analytics, properties, alerts/reports, prompts, datasets, caching, limits/fallbacks | Cost charts and gateway controls are common expectations; Cutback needs a useful path from a cost observation to a measured application change. Plan availability differs. |
| [Portkey Gateway](https://github.com/portkey-ai/gateway) | Provider routing, reliability configuration and guardrails | Do not compete on a provider-count claim. Qualify a smaller set carefully and let customers retain their gateway. |
| [LiteLLM](https://docs.litellm.ai/docs/proxy/provider_budget_routing) | Provider/model/tag budget routing and shared Redis budget tracking | Budget controls are essential infrastructure, not a unique selling point. Preserve Cutback's durable pre-call experiment reservations. |
| [TensorZero](https://www.tensorzero.com/docs/optimization) | Model fine-tuning integrations, GEPA prompt optimization, inference-time strategies and custom optimization recipes | Automatic optimization and multiple strategies already exist. Cutback must connect its optimization to existing application source and independent acceptance evidence. This page was available through the search index; direct retrieval was robots-blocked. |
| [Opik Agent Optimizer](https://www.comet.com/docs/opik/development/optimization-runs/overview) | Multiple optimizers, parameter tuning, tool/agent optimization, trial evidence and local execution | “Automatic prompt optimization” cannot be presented as novel. A hardcoded cheaper-model pair is a major functional gap. |
| [Bifrost](https://github.com/maximhq/bifrost) | Gateway failover, load balancing, semantic caching, virtual keys, budgets and MCP Code Mode | Generic cost reduction and MCP overhead controls are competitive territory too. A semantic cache needs workload/privacy evidence; it is not interchangeable with Cutback's scoped exact cache. |
| [Promptfoo](https://www.promptfoo.dev/docs/configuration/expected-outputs/) and [red teaming](https://www.promptfoo.dev/docs/red-team/quickstart/) | Deterministic/model-graded assertions, cost/latency checks, CI and adversarial testing | Ordinary accuracy does not cover security or agent behavior. Provide explicit acceptance gates and interoperate with established security evaluation. |
| [Not Diamond](https://docs.notdiamond.ai/docs/what-is-model-routing) | Quality/cost/latency routing, pretrained and customer-trained routers | A byte-length routing rule is a bounded mechanism, not a competitive learned-router claim. Measure route quality and all routing overhead. |
| [RouteLLM](https://github.com/lm-sys/RouteLLM) | Open routing framework, trained routers and benchmark/evaluation tooling | Routing and cost/quality trade-offs have existing open implementations. Published benchmark results are specific to their datasets; they are not Cutback savings claims. |
| [DSPy](https://dspy.ai/) | Composable LM programs and optimization from task examples/metrics | Integrating a qualified optimizer may be better than implementing every search algorithm. Do not make the scanner depend on it. |

No ranking by stars, funding or vendor marketing savings was used. This review does not establish competitive superiority, market size, willingness to pay or fundraising prospects.

### Closest overlap: code-changing agents already exist

[Opik's product page](https://www.comet.com/site/products/opik/) advertises source-code fixes through Ollie, natural-language test assertions and local Agent Playground execution. Its [pricing/capability page](https://www.comet.com/site/pricing/) describes `opik connect`, proposed edits subject to review and rerunning an agent against a failing trace. These are documented vendor capabilities, not a hands-on acceptance result. An agent that reads traces and proposes a code fix is therefore **not an established Cutback differentiator**.

Opik also documents a separate [Cost Intelligence](https://www.comet.com/docs/opik/cost-intelligence/overview) product: a local per-developer forwarding daemon with metadata-only reporting and coding-agent cost attribution/policy controls. Local execution and metadata privacy alone do not establish uniqueness either. Cutback's application-SDK scanner/collector architecture differs, but architecture differences matter commercially only if they produce a better customer outcome.

The narrower hypothesis is a portable optimization change record: exact native request/source fidelity, explicitly budgeted search, an independently confirmed selected change, a reproducible diff/rollback, and measured cost per successful application task while retaining the customer's existing tools. Several components already exist elsewhere. The combination must win an observed comparison; it is currently a hypothesis, not a moat.

## Common-feature audit

“Implemented” below means bounded functionality exists in this repository. It does not mean it matches a mature competitor's scale, breadth or usability. Acceptance details remain in [progress](progress.md).

| Capability | Actual Cutback state | Code/evidence and next gap |
|---|---|---|
| Independent offline discovery | Implemented, Python-first; optional JS syntax analysis | `scanner/`, `core/request_profiles.py`; dynamic/framework coverage remains explicit |
| SDK telemetry, cost and latency | Implemented for pinned SDK surfaces | `instrumentation/`, `pricing.py`; more providers require native qualification |
| Session/trace investigation | Partial | Trace/span metadata and workflow grouping exist; rich multi-step timeline and tool/retrieval investigation need work |
| Filtering, overhead separation, usage reports | Implemented within bounded queries | `server/repository.py`, `operations.py`; cost attribution is not invoice reconciliation |
| Dataset versions, provenance and grouped splits | Implemented | `evals/datasets.py`, `records.py`; adapters for existing evaluation/trace exports remain needed |
| Deterministic/structured metrics | Implemented | `evals/metrics*.py`; general conversational and application-level quality need extension |
| Judge evaluation and diagnostics | Implemented, provisional evidence | `evals/experiments.py`; customer calibration remains required |
| Human failure review/corrections | Partial | `server/case_evidence.py`; assignment queues, adjudication and reviewer calibration remain needed |
| Prompt/model experiments and comparisons | Implemented | Native frozen contracts, criteria, slices and paired deltas; automatic agent path previously selected only a qualified cheaper-model pair |
| Prompt registry/playground | Partial | Frozen encrypted request artifacts and named recipes exist; an interactive versioned prompt authoring/promote workflow does not |
| Automatic multi-strategy search | Gap addressed incrementally | First increment: model and output-cap portfolios under one frozen budget. Prompt generation, learned routing, compression and optimizer integration remain separate work |
| Cost/quality/latency frontier | New implementation increment | Preserve failed/unknown candidates and show trade-offs; descriptive sample frontier is not independent post-selection confirmation |
| Source changes and rollback | Partial | `changes.py`; source-bound model/config edits exist. Expand safe transformations and application-level regression qualification |
| CI regression enforcement | Partial, new CLI gate | `workflow gate` checks canonical heldout evidence and current source; turnkey customer PR annotations and CI setup remain needed |
| Live quality/drift/alerts | Partial | Schedules, health and rollout windows exist; online evaluation and actionable drift/alert lifecycle need implementation |
| Cache, routing and training | Implemented mechanisms, limited automation | `cache_api.py`, `executors.py`, `training.py`; workload eligibility, selection and measured application impact need integration |
| Budget/governance | Implemented for experiments | Frozen approval, shared quotas, reservations and uncertain-charge stops; application gateway spend enforcement is a different capability |
| Payload privacy and data control | Implemented within documented limits | Metadata default, authorized encrypted content, retention/export/delete; real storage/organization acceptance remains external |
| Teams, roles, identity and audit | Implemented optional foundation | `organization_api.py`, `identity.py`; real customer IdP and operational use remain unqualified |
| Security/red teaming | Partial | Product security tests and content policy exist; an application red-team product is not implemented |
| RAG, tools and multi-turn application evals | Gap | Current source-bound replay deliberately rejects tools, streaming and multimodal contracts |
| Reliability and deployment packaging | Implemented references/local tests | Compose/Helm/Terraform, migrations/backup; no managed service or production SLA |
| Easy first experience | Partial | Independent sample and installed-wheel local stack work; blind first-user trials still needed |
| Measured customer ROI | Gap | Synthetic sample cost deltas exist; no real customer savings, adoption, retention or paid demand established |

## Differentiator to earn

Build an optimization change workflow that connects **source → observed workload → permitted examples → bounded search → independent quality evidence → exact application diff → monitored outcome**. Keep the provider SDK and existing observability stack. The agent does the engineering work and asks the owner only for decisions it cannot legitimately infer.

Its defensibility would come from reliable source adapters, reusable workload-specific acceptance contracts, failure knowledge and measured change outcomes, with customer permission. A generic chat interface, a Pareto chart, an optimizer wrapper or an integration count is not a moat. Never aggregate customer payloads or claim a data network without permission.

An example target journey: a developer asks their existing coding agent to optimize a support classifier. Cutback discovers its native request and fixtures, proposes several qualified changes, freezes the complete cost ceiling, compares them on declared criteria, explains rejected cheap variants, and prepares a reversible diff against the exact source. An independently approved holdout/staging check then determines whether to ship. A later measured window tests whether the benefit survives real traffic. The last steps are not yet a completed automatic product.

## Implementation sequence and acceptance

| Order | Work | Completion gate |
|---|---|---|
| 1 | Implemented in this source increment: bounded model/output-cap portfolio, sample frontier, quality/accounting rejection reasons, source-bound cap/model proposal | Local synthetic native SDK → collector → PostgreSQL → worker → report → reversible diff passes; see [acceptance](evidence/search-acceptance.json). Real provider/customer qualification remains open |
| 2 | Implemented in this source increment: separate search/confirmation lifecycle, immutable selected candidate and untouched confirmation groups | Disjoint validation/test inputs, changed-contract rejection, exact new approval and concurrent queue rejection pass. Existing heldout criteria still determine evidence strength; staging is separate |
| 3 | Existing-evidence import and application regression contracts | Qualified adapters, permission/tenant isolation, unmapped fields stay unknown; agent runs explicit application checks in the application's own workspace |
| 4 | Agent-led prompt and structured-output optimization | Candidate generation only from permitted training/validation data; generator spend shares budget; frozen exact prompt/config diff; independent heldout confirmation |
| 5 | Workflow cost/quality outcomes and safe continuous optimization | Approval-bounded observation, changes linked to versions, drift/rollback reasons, overhead and unpriced traffic shown; no automatic production changes without authorization |
| 6 | Workflow-specific RAG/tool/multi-turn contracts | Native executor and source adapters with correct tool semantics and application outcome criteria; no generic replay of arbitrary application code |
| 7 | First-use polish and interoperability | A new user can finish the independent sample without maintainer assistance, explain the result, and complete one real authorized application trial |

Rows are engineering priorities, not completed-feature claims or reliable calendar estimates. Existing features must remain usable throughout; do not replace PostgreSQL, weaken privacy, mutate released migrations or widen SDK support to make a demo work.

## Customer and launch evidence still needed

Use the same rights-cleared application task to compare Cutback with the customer's incumbent process and a relevant competitor workflow. Predeclare the acceptance criteria and independent confirmation groups. Record time to the first useful reviewed change, human interventions, source/request fidelity, all optimizer/judge/target-call overhead, cost completeness, quality regressions, and ability to reproduce/roll back the result. Include a shared-config regression and an uncertain-charge interruption. Treat an unavailable measurement as unknown. Do not substitute provider token counts for invoice or infrastructure costs, or synthetic correctness for a real business outcome. A hands-on comparative trial requiring accounts, private data or paid inference needs the relevant authorization first.

Start with engineers already paying meaningful inference bills for bounded text/structured workflows, who can supply rights-cleared examples and independent success criteria. This is a proposed initial customer profile, not validated demand. Interview and observe several such teams before generalizing to all agents.

Record installation completion, time to first useful proposal, unresolved source reasons, review effort, experiment overhead, accepted/rejected changes, independent quality outcomes and realized cost per successful task. Report denominators and failures. Repeated successful use and willingness to pay matter more than generated feature count. Do not set fabricated adoption, revenue, star or savings numbers.

The owner must supply or authorize real application/vendor/data/budget decisions, customer outreach, production traffic and public launch claims. Engineering can prepare and test all reversible local work first. No paid inference, customer outreach or production deployment was authorized by this research.
