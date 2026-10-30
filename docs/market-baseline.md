# Cutback's market baseline

Market audit: 27 October 2026; implementation reconciliation: 30 October 2026. [Current installation and remaining work](status.md). This replaces a feature-name checklist with user-visible acceptance requirements. The [source review](research/market-sources-2026-10-27.md) supports market availability; the paths below support Cutback's actual state. Requirements are additive to the master engineering specification and repository privacy/security rules. They do not authorize paid calls, production changes, arbitrary tool execution or publication of private applications.

**Finding: Cutback is not yet a complete, competitive evaluation and monitoring platform.** It has useful bounded mechanisms, but important everyday journeys are incomplete. Passing its current test suite proves those tested contracts; it does not prove this entire baseline. No percentage-complete score is calculated by counting unequal rows.

## How to read the audit

- **Bounded:** implemented for the documented scope, with acceptance evidence in `docs/progress.md`. Broader parity is not implied.
- **Partial:** some mechanism exists, but the stated user journey has a material missing step.
- **Missing:** no qualifying implementation was found in this audit. A similarly named module does not count.
- **External:** requires real customer, deployment, account, scale or user evidence; synthetic tests cannot establish it.

**Core baseline** means required for the intended existing-application text/structured/agent workflow. **Platform expansion** means common in broader mature platforms and part of the desired eventual product, with its own release gate. **Specialty** means available in a specialist segment and needing a separate adapter, threat model or operating contract. These are sequencing labels, not permission to silently drop functionality. Voice simulation, GPU monitoring, a visual application builder and enterprise provisioning are not interchangeable with ordinary evaluation; combining them into one undifferentiated “everything” milestone would hide unfinished work.

Each row states a checkable acceptance requirement. If the current implementation cannot perform that requirement, the row remains partial or missing even if a demo resembles it. Paths are repository-relative; they are implementation anchors, not claims that every requirement already has a passing test.

## 1. First use and existing-tool integration — core baseline

Evidence for expectations: [Phoenix](https://github.com/Arize-ai/phoenix), [Braintrust](https://www.braintrust.dev/docs), [Opik](https://www.comet.com/docs/opik/ollie), [Coval](https://docs.coval.ai/welcome).

| ID | Acceptance requirement | Cutback state | Current anchor / remaining work |
|---|---|---|---|
| F01 | Install and run offline discovery without an account, SDK, server or paid call | Bounded | `scanner/`, `core/request_profiles.py`; core wheel contract tests |
| F02 | One independent sample demonstrates installation → observation → failing check → reviewed improvement | Bounded | Independent support-desk and source campaign/outcome applications exercise native synthetic observation, PostgreSQL, rejected candidates, application checks and exact reviewed changes; [versions and limits](status.md). Unfamiliar-user acceptance remains F03. |
| F03 | An unfamiliar developer completes the sample without maintainer intervention and explains why a candidate failed | External | Automated sample evidence exists; observed first-user trials do not |
| F04 | Existing coding agent can discover commands, prepare work and resume an interrupted workflow | Bounded | `agent.py`, `cli_agent.py`, packaged agent workflows; client discovery still needs independent acceptance |
| F05 | Setup diagnoses unsupported source/request contracts and offers a concrete next action | Partial | Request-profile rejection reasons exist; guided resolution/adapters remain narrow |
| F06 | Read an existing OTel/OpenInference producer without requiring a new model client | Bounded | `server/workflow_otel.py`, pinned OpenTelemetry 1.45.1 / OpenInference 0.1.42 producer; explicit bounded JSON metadata projection, not all exporters |
| F07 | Import a qualified existing trace export while preserving provenance and unknown fields | Bounded | `evals/importers.py`: actual pinned OpenInference OTLP JSON projection, source hash/mapping/rejections; no universal Phoenix/Langfuse/Braintrust export claim |
| F08 | Import an existing evaluation suite with declared inputs/expected outputs/criteria | Bounded | Actual Promptfoo 0.125.1 version-3 result projection, literal unreviewed expectations, historical output and externally asserted score/error distinctions; no external code or unsupported criteria execution |
| F09 | Produce a portable result consumable in another workspace or CI runner | Bounded | Canonical reports plus source-bound `checks` JSON/JUnit for two independent Git workspaces; local receipts are not independent authenticity attestation |
| F10 | Versioned documentation tells installed-wheel users which commands are unreleased | Bounded | `docs/optimization-search.md`, release artifact validation; maintain on every release |

## 2. Trace, session and workflow investigation — core baseline

Evidence: [OpenInference](https://arize-ai.github.io/openinference/spec/), [Phoenix tracing](https://arize.com/docs/phoenix/tracing), [Langfuse sessions](https://langfuse.com/docs/observability/features/sessions), [Fiddler](https://docs.fiddler.ai/getting-started/agentic-monitoring).

| ID | Acceptance requirement | Cutback state | Current anchor / remaining work |
|---|---|---|---|
| T01 | Observe supported native LLM calls without altering native response/stream semantics | Bounded | Pinned Python and Node native SDK qualification in `instrumentation/` and `packages/js-sdk/`; exact versions/surfaces in [providers](providers.md), not universal wrappers. |
| T02 | Retain agent, chain, tool, retrieval, reranker and evaluator steps with parent identity | Bounded | `core/traces.py`, `server/trace_db.py`; generic metadata spans separate from priced native attempts |
| T03 | View one trace as an ordered tree/waterfall with per-step timing and status | Bounded | `web/src/traces.tsx`; authenticated tree/waterfall, nanosecond-safe timings, explicit page limits |
| T04 | Group multiple traces into a conversation/session with ordering and pagination | Bounded | Environment-scoped session/trace associations and receipt-time keyset pagination; capture coverage remains unknown |
| T05 | Preserve late/out-of-order spans and show missing parents without inventing edges | Bounded | Immutable scoped identities, late parents/results, visible cycles/orphans/skew/truncation; real PG concurrency tests |
| T06 | Distinguish unknown status, transport success and business-task success | Bounded | Unknown/OK/error/incomplete execution status is separate from externally asserted task outcome; business success stays unknown |
| T07 | Correlate retries with logical operations while preserving aggregate external-span uncertainty | Bounded | `core/models.py`, `server/repository.py`; external OTLP cannot infer attempts inside a span |
| T08 | Filter/search recent traces by time, environment, workflow, model, error, session and release | Bounded | Trace search by scoped environment, observed start window, workflow/service/release/native model/error/session; retained data only |
| T09 | Compare a failed workflow with a successful one and inspect where their paths diverged | Bounded | Same-environment retained operation/kind ancestry/occurrence comparison, explicit unmatched/truncated paths; task quality not inferred |
| T10 | Display permitted content through purpose-scoped reads, while metadata-only users see useful structure | Bounded | Separate opt-in encrypted span input/output, current policy/sampling/quota/expiry, audited purpose and admin-only reads; viewers retain useful structure |

## 3. Operational monitoring and incident response — core baseline / platform expansion

Evidence: [Datadog](https://docs.datadoghq.com/llm_observability/), [Logfire live evals](https://pydantic.dev/docs/logfire/evaluate/live-evals/), [MLflow monitoring](https://mlflow.org/ai-monitoring/), [Helicone](https://docs.helicone.ai/getting-started/quick-start).

| ID | Acceptance requirement | Cutback state | Current anchor / remaining work |
|---|---|---|---|
| O01 | Query token/cost/latency/error trends with explicit priced and observed denominators | Bounded | `server/repository.py`, `server/operations.py`; modeled rates, not invoice reconciliation |
| O02 | Separate application traffic from evaluation, judge and optimizer overhead | Partial | Application/evaluation/judge dimensions and canonical campaign generation/failed-trial/confirmation receipts exist; additional optimizer adapters need the same accounting before qualification. |
| O03 | Expose exporter drops/outages and avoid presenting partial telemetry as full population coverage | Bounded | SDK health/collector reports; coverage remains unknown where not measurable |
| O04 | Define immutable/versioned online evaluation rules with sampling, scope and a budget ceiling | Bounded | Scoped immutable metadata/content rules, deterministic sampling, native judging through the canonical pre-call ledger, durable request/dollar caps and no replay of uncertain charged leases; [limits](online-quality.md). |
| O05 | Join delayed scores/feedback to the exact span, trace or session without tenant ambiguity | Bounded | Versioned immutable external result IDs, exact project/environment/trace/span/session targets and delayed joins |
| O06 | Distinguish evaluator errors, missing data, invalid scores and actual application failures | Bounded | Metadata/content/judge receipts separate failed/error/missing/unknown, invalid/ungrounded output, lost authority, exhausted budgets and uncertain billing. External ingestion cannot forge internal provenance; [limits](online-quality.md). |
| O07 | Create alert rules with windows, minimum sample size, cooldown, deduplication and recovery | Bounded | Source closed event-time windows, durable late-result/backfill re-evaluation, minimum samples/cooldown/dedup; no-data/incomplete never establish recovery. Acceptance/publication gates in progress.md |
| O08 | Acknowledge/resolve an incident with owner, evidence links and an audit trail | Bounded | Source incident CAS/history/owner routing references, retained receipt → trace investigation and existing authorized Review path; synthetic acceptance only |
| O09 | Deliver configured webhooks/notifications with signed payloads, retries and delivery status | Bounded | Default-disabled metadata-only signed HTTPS profile, project/operator allowlists, public IP pinning/original TLS host, exact config approval, durable capped at-least-once attempts/dead letters. Synthetic receivers only; no external delivery configured |
| O10 | Diagnose drift by cohort/release and distinguish input drift from labeled quality loss | Partial | Source exact-version observed-window comparisons separate technical contracts, judge estimates and missing/errors; release/service and opt-in coarse input-size distributions retain missing categories. Semantic drift, significance, causation and population coverage are not inferred |

## 4. Datasets, provenance and review — core baseline

Evidence: [Braintrust review](https://www.braintrust.dev/docs/annotate/human-review/manage-review-work), [labels](https://www.braintrust.dev/docs/annotate/labels), [MLflow datasets](https://mlflow.org/docs/latest/genai/datasets), [Cleanlab annotators](https://docs.cleanlab.ai/v2.5.0/tutorials/multiannotator.html).

| ID | Acceptance requirement | Cutback state | Current anchor / remaining work |
|---|---|---|---|
| D01 | Create frozen dataset versions with stable case identities and import provenance | Bounded | `evals/datasets.py`, `evals/records.py`, `server/evaluation_api.py` |
| D02 | Distinguish independent gold labels, historical references, generated cases and user feedback | Bounded | Dataset attestations/evidence tiers; feedback is not automatically gold |
| D03 | Prevent duplicate/customer groups leaking between train/validation/test | Bounded | Persistent cross-version normalized-input/customer families, transitive merges, pre-call exposure/protected confirmation and legacy abstention; semantic duplicates are not universally detected |
| D04 | Show split/class/stratum coverage and refuse inadequate independent evidence | Bounded | Dataset split metadata and verification criteria |
| D05 | Turn a permitted production failure into a reviewed new regression dataset version | Bounded | Captured trace content → frozen blind review → immutable resolution → new dataset; requires active rights and actual captured input, with synthetic acceptance only |
| D06 | Assign review items and safely claim/release work with reviewer authorization | Bounded | `server/review_api.py`: current scoped-key/OIDC assignments, bounded queues, concurrent quorum claims, expiry/release/revocation and explicit queue closure |
| D07 | Collect independent blind reviews without showing earlier reviewers' answers | Bounded | Server withholds peer answers/source gold; immutable votes and separate adjudicator; separate credentials are not proven separate humans, independence remains owner-attested |
| D08 | Detect disagreement and adjudicate without overwriting original judgments | Bounded | Exact categorical consensus or designated adjudication adds a resolution while preserving every original vote; no averaged categorical gold |
| D09 | Version rubrics and measure judge/reviewer agreement on an untouched calibration set | Partial | Frozen rubric/assignments, blind known-gold calibration and completed exact-match counts; queues cannot promote; independent judge/customer calibration qualification remains open |
| D10 | Track item lineage through import, review, correction, evaluation and deletion | Bounded | Qualified source hashes, review/source/vote/resolution receipts, frozen assignment hashes and retained exposure identities with project export/deletion; unsupported tools/semantic identity remain unqualified |

## 5. Offline evaluations and regression enforcement — core baseline

Evidence: [Promptfoo assertions](https://www.promptfoo.dev/docs/configuration/expected-outputs/), [Giskard checks](https://docs.giskard.ai/oss/checks), [LangSmith evaluation](https://www.langchain.com/langsmith/evaluation), [Google evaluation](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/evaluation-overview).

| ID | Acceptance requirement | Cutback state | Current anchor / remaining work |
|---|---|---|---|
| E01 | Run deterministic text/label/structured criteria with exact evaluator versions | Bounded | `evals/metrics.py`, `metrics_extra.py`, frozen plans |
| E02 | Keep technical validity separate from semantic quality and task completion | Bounded | Evidence tiers and criteria; nonempty/schema is not correctness |
| E03 | Run approved model judges with frozen rubrics, invalid-vote handling and complete costs | Bounded | `evals/experiments.py`; calibration remains external |
| E04 | Compare paired baseline/candidate cases and slices with grouped uncertainty | Bounded | `evals/comparisons.py`, `server/case_evidence.py`; sample adequacy still matters |
| E05 | Repeat stochastic tasks and expose variation without hiding failed attempts | Bounded | Experiment repetitions and cost/completeness reporting |
| E06 | Run the application's real local regression contract against a proposed change | Bounded | `application_checks.py`: explicit Python/Node argv against frozen base/proposed trees, minimal fixture environment, process/time/output limits; no OS sandbox |
| E07 | Accept precomputed outputs without secretly rerunning an application/model | Bounded | External scalar results plus qualified native/OpenInference/Promptfoo historical outputs import with zero inference; source assertions remain unverified and unsupported evaluator code never executes |
| E08 | Produce machine-readable CI/JUnit outcomes with exact artifact/source provenance | Bounded | Source/contract/runtime-bound metadata JSON/JUnit, stale/tampered/mutating-tree rejection and independent-workspace verification; CI authenticity remains external |
| E09 | Compare changed and unchanged workflows when a shared config/prompt is modified | Partial | Source consumer warnings and pinned registry dependencies/uses; public parse-only discovery prepares full-suite or explicitly declared portable commands. Selective affected-consumer scheduling and complete coverage remain unverified. |
| E10 | Reproduce an experiment from pinned data, evaluator, native request and price snapshots | Bounded | Frozen native contract; identical stochastic outputs are not promised |

## 6. Prompt development and release — core baseline / platform expansion

Evidence: [MLflow registry](https://www.mlflow.org/genai/prompt-registry/), [Maxim optimization](https://www.getmaxim.ai/docs/prompt-engineering/prompt-optimization), [Logfire](https://pydantic.dev/docs/logfire/get-started/).

| ID | Acceptance requirement | Cutback state | Current anchor / remaining work |
|---|---|---|---|
| P01 | Author encrypted text/chat prompt versions with stable immutable identities | Bounded | PostgreSQL immutable versions, salted encrypted content receipts, client UUID/idempotence and ordinal CAS; dashboard authoring |
| P02 | Declare template variables/types and validate missing/extra variables deterministically | Bounded | Limited declarative text/chat templates, exact string/integer/boolean/JSON variables, local rendering and bounded composition; no Python/Jinja/includes |
| P03 | Diff versions and link prompts to source, experiments and observed releases | Partial | Purpose-bound diffs, immutable experiment bindings and externally reported releases; source provenance references are manual and source-patch integration remains separate |
| P04 | Compare prompt/model/parameter variants in a playground under explicit total budgets | Bounded | Playground compiles exact native versions/variables/supported sampling/schema into separately approved plans; no browser inference or hidden calls |
| P05 | Reuse prompt components with dependency/version tracking and cycle detection | Bounded | Pinned text components in text/chat roots, transitive current rights, cycle/depth/size/type-conflict checks and consumer indexes |
| P06 | Promote an exact reviewed prompt version through environment aliases with concurrency protection | Bounded | Expected-version/revision CAS, immutable approvals/evidence receipts; manual/synthetic quality remains unverified |
| P07 | Resolve a pinned prompt/alias via scoped credentials without returning unauthorized content | Bounded | Dedicated environment-scoped prompt readers, per-component expiring grants, current policy/OIDC checks, purpose audits and verified local graph rendering |
| P08 | Roll back an alias and show which application releases still use an older version | Partial | Exact latest-receipt alias rollback and retained release/version reports exist; reports have unknown coverage and do not establish a complete current deployment inventory |
| P09 | Pin generated rubrics/test criteria before evaluation instead of changing the goal during search | Partial | Plan hashes freeze criteria; automatic rubric generation is not implemented |
| P10 | Test structured output/tool schemas alongside prompt changes, including negative cases | Partial | Frozen JSON-schema and negative/refusal/incomplete checks plus qualified native synthetic function conversations; general real application/tool schema execution and additional model/surface coverage remain unfinished. |

## 7. RAG, tools and conversation evaluation — core baseline for agent claims

Evidence: [Ragas](https://docs.ragas.io/en/latest/concepts/metrics/available_metrics/), [DeepEval](https://documentation.confident-ai.com/concepts/metrics), [τ²-bench](https://arxiv.org/abs/2506.07982), [MLflow multi-turn](https://mlflow.org/docs/latest/genai/eval-monitor/running-evaluation/multi-turn/).

| ID | Acceptance requirement | Cutback state | Current anchor / remaining work |
|---|---|---|---|
| A01 | Represent conversation turns, tool calls/results, retrieval context and final outcome separately | Bounded | Typed events, native sync text/function turns and separate outcomes in `core/episodes.py`, `core/agent_turns.py`, `evals/episode_runner.py`; [scope](agent-evaluation.md). |
| A02 | Score retrieval precision/recall/ranking with declared relevance judgments | Bounded | Connected synthetic RAG execution/review with declared precision, recall and nDCG in `evals/episode_metrics.py`; unjudged documents preserve unknown coverage. General corpus/production retrieval adapters remain unqualified. |
| A03 | Score groundedness/citations separately from answer correctness | Partial | Literal citations and exact references remain separate. Opt-in v5 judges add separately scoped answer/returned-document/conversation assessments, verified exact quotes and judge costs ([contract](episode-semantic-judging.md)); semantic scores remain Tier 3 proxies. Independent semantic citation correctness and production retrieval acceptance remain unqualified. |
| A04 | Check required/forbidden tools, arguments, ordering and duplicate side effects | Bounded | Declared synthetic tools: required/forbidden actions, exact arguments, paths and duplicate effects are replay-checked; atomic fixture updates and native loop/denial tests. Real application tools are not executed. |
| A05 | Permit multiple valid tool paths while asserting final environment state | Bounded | Alternative permitted tool paths plus separately asserted final synthetic state. Does not verify a real external environment. |
| A06 | Test multi-turn memory, constraints, clarification, handoff and task completion | Partial | Native multi-turn ordering, scripted follow-ups and exact per-user-turn constraints are implemented. Generative user/persona simulation and real handoff integrations remain missing. |
| A07 | Handle maximum steps, loops, timeout, cancellation and uncertain side effects safely | Bounded | Per-turn PostgreSQL reservations, turn/tool/request/USD caps, cancellation/revocation checks, expired-call uncertainty and late-cost handling. Already dispatched vendor work cannot be recalled by a local timeout. |
| A08 | Replay declared synthetic/read-only tool fixtures without running arbitrary production tools | Bounded | Exact declared read-only/state fixture runner performs no network, files, shell or application execution; scanner remains parse-only. Real tool sandboxes need separate qualification. |
| A09 | Generate task-aware scenarios from permitted data with human-reviewable coverage | Partial | Versioned offline expansion of owned synthetic episode cases with anchors, follow-up/injection variants, coverage mapping and review requirements. Generated variants share source lineage; generative/real-data scenario building remains missing. |
| A10 | Evaluate security and benign task utility in the same scenario | Partial | Declared utility assertions run with unauthorized-tool and retrieved-instruction injections in the same synthetic workflow. External evaluation import is qualified separately; this is not a general red-team or guardrail integration. |

## 8. Optimization and change automation — core product outcome

Evidence: [Opik Optimizer](https://www.comet.com/docs/opik/development/optimization-runs/overview), [Ollie](https://www.comet.com/docs/opik/ollie), [GEPA](https://github.com/gepa-ai/gepa), [DSPy](https://dspy.ai/), [Not Diamond](https://docs.notdiamond.ai/docs/what-is-model-routing).

| ID | Acceptance requirement | Cutback state | Current anchor / remaining work |
|---|---|---|---|
| C01 | Discover qualified native request/source dependencies and bind evidence to them | Bounded | `core/request_profiles.py`, scanner, encrypted plan artifacts; dynamic cases abstain |
| C02 | Search more than one model/output bound under one frozen cost/request ceiling | Bounded | `evals/search.py`, agent search/confirmation; at most five deterministic candidates |
| C03 | Generate and evaluate task-specific prompt/structured-output improvements | Partial | Source campaign implements bounded native instruction/output-cap generation, adaptive validation, frozen confirmation and exact literal source edits; campaign release acceptance is in progress.md. Structured-output schemas stay frozen; arbitrary schema optimization is not qualified |
| C04 | Optimize retrieval/chunking/reranking/tool selection under task-quality constraints | Partial | [Actual bounded local corpus search](retrieval-optimization.md), automatic chunk/overlap/top-k/lexical-rerank alternatives, native answer-quality evaluation, untouched confirmation and settings-bound diffs/checks. Qualification/publication is recorded in progress.md. Tool selection, remote/vector adapters and learned rerankers remain unfinished. |
| C05 | Select among exact cache, routing, prompt changes and training using measured workload eligibility | Partial | Complete task costs bound a versioned model/output-cap-first campaign with instruction fallback under one budget; static passes skip generation, unchanged controls skip duplicate trials, and confirmation remains untouched. Unified selection across exact cache, routing, retrieval and training is still absent |
| C06 | Count generator/judge/failed trial/routing overhead in the optimization economics | Partial | Canonical cumulative improvement receipts and source campaign parent/child reservations include generation and all evaluated/rejected trials; retained missing campaign context stays unknown. Remaining optimizer/routing adapters need their own qualification |
| C07 | Independently confirm the selected change on disjoint untouched groups | Bounded | Validation then separately approved untouched confirmation; persistent project-level normalized-input/customer families and exposure receipts prevent reimports resetting known holdouts. Unknown historical exposure and semantic identity remain conservative; no universal independence proof. |
| C08 | Produce an exact source-bound diff, affected-consumer explanation and reversible rollback | Partial | Literal model/output-cap and source campaign literal instruction replacement/insertion; exact UTF-8/AST binding and reversible patches. Dynamic/imported instruction, framework/tool adapters and unresolved consumers remain explicit gaps |
| C09 | Run the application's checks and prepare a reviewable branch/PR without deploying | Partial | Portable checks bind both exact source trees; public full-suite/explicit-command discovery and campaign review can prepare a non-overwriting local branch with hooks disabled. Selective coverage, extra-file source adapters and remote PR publication remain separately scoped. |
| C10 | Measure accepted change outcomes over later traffic and recommend rollback on regression | Partial | Public finite approved metadata observation joins exact reviewed source to declared later task-cost windows, preserves all campaign costs and prepares rollback review. Deployment verification, automatic rollback and independent later-outcome validation remain unfinished; [contract](automatic-outcome-observation.md). |

## 9. Gateway and performance controls — platform expansion

Evidence: [LiteLLM](https://docs.litellm.ai/docs/proxy/virtual_keys), [Portkey](https://portkey.ai/docs/product/guardrails), [Bifrost](https://github.com/maximhq/bifrost), [Helicone](https://docs.helicone.ai/getting-started/quick-start).

| ID | Acceptance requirement | Cutback state | Current anchor / remaining work |
|---|---|---|---|
| G01 | Retain customers' provider SDK/gateway while observing and proposing changes | Bounded | Core architecture; native qualification, not universal proxy compatibility |
| G02 | Enforce application key/team/model budgets across concurrent callers | Partial | Working source adds finite project/environment virtual keys, shared project/team/model/key modeled counters, pre-call reservations and current authority accepted in PR 33 with installed and all ten public gates. Initial Luna Responses text only; broader provider and organizational coverage remains unfinished. [Contract](gateway-budgets.md) |
| G03 | Configure retries/fallbacks/timeouts without duplicating uncertain billable or side-effecting actions | Partial | Working text gateway uses explicit deadlines, stable UUIDs, durable uncertainty and nonretryable responses; explicit policy recovery retains every limit and never retries a call. General configurable retries/fallbacks/side-effect profiles remain unqualified. [Contract](gateway-budgets.md) |
| G04 | Run exact scoped encrypted caching with expiry and explicit eligibility | Bounded | `server/cache_api.py`; does not imply universal automatic cache selection |
| G05 | Qualify semantic caching against false hits, privacy boundaries and task regressions | Partial | A separate supplied-vector engine, encrypted frozen catalog, scoped runtime lookup and validation/confirmation gates now work through actual PostgreSQL and the authenticated dashboard ([scope](semantic-cache.md)); final installed, archive/image and hosted acceptance are unfinished. Real embedding quality is not established by synthetic controls. |
| G06 | Compare learned routing with baseline/heuristic routing including router costs and quality | Partial | Byte-length route mechanism exists; trained/custom-router acceptance absent |
| G07 | Measure streaming first-token latency/cancellation and account for partial usage | Partial | Native stream observation plus an explicit Luna gateway SSE/first-nonempty-text-delta timing extension publicly accepted in PR 39; [exact evidence](progress.md). Unknown partial usage keeps its hold; measured gateway timing is not client end-to-end latency. Cross-provider behavior remains unqualified. |
| G08 | Batch/schedule eligible work with measured turnaround and full cost attribution | Partial | Native Responses validation batches, canonical per-item reservations, turnaround, explicit provider storage, cancellation/recovery and file cleanup are publicly merged after installed/hosted acceptance ([scope](provider-batches.md), [receipt](evidence/provider-batch-acceptance.json)). Full application cost attribution and automatic cross-engine selection remain unknown/unimplemented; the ordinary queue alone never established this feature. |
| G09 | Evaluate context compression/token reduction against original task behavior | Bounded | Actual original/deduplicated/query-selected native inputs, quality/CPU/component economics, untouched confirmation and source settings/check/rollback workflow accepted in PR 41; [contracts](context-compression.md), [exact evidence](evidence/context-compression-acceptance.json). Learned/generative transforms and arbitrary dynamic application builders remain unqualified. |
| G10 | Measure local-model/GPU costs without assuming public API rates equal hardware economics | Partial | Bounded local training/compute assumptions; live GPU/infrastructure metering absent |

G09 follow-up: working source now compares actual original, duplicate-line and query-selected inputs through a frozen native model and the durable experiment ledger. Native/CPU component costs, independent confirmation, encrypted input review and checked settings diffs are implemented; the acceptance state is recorded in [progress](progress.md). This checkpoint does not mark the broader compression market segment complete or qualify learned/generative transforms and arbitrary source builders.

## 10. Privacy, security and governance — core baseline

Evidence: [OpenInference privacy](https://arize-ai.github.io/openinference/spec/), [Invariant](https://github.com/invariantlabs-ai/invariant), [NeMo](https://docs.nvidia.com/nemo/guardrails/about-nemo-guardrails-library/overview), [Promptfoo](https://www.promptfoo.dev/docs/red-team/).

| ID | Acceptance requirement | Cutback state | Current anchor / remaining work |
|---|---|---|---|
| S01 | Metadata by default; arbitrary prompts/outputs/headers/exception text never silently enter the collector | Bounded | Strict event/profile schemas and privacy tests; maintain for new span/eval types |
| S02 | Store permitted content encrypted with rights, purpose, scope, expiry and quotas | Bounded | `content_policy.py`, `evals/artifacts.py`, capture/import policy |
| S03 | Enforce tenant/environment/RBAC scope on every query/mutation/content read | Bounded | Server scope guards and PostgreSQL tenant tests; new endpoints require the same |
| S04 | Rotate/revoke/expire credentials; browser tokens never persist in URLs/storage/logs | Bounded | Access keys, OIDC/PKCE, authenticated browser checks |
| S05 | Export/delete all new records and content with auditable receipts and retention | Bounded | `privacy_api.py`, maintenance; new tables must be added and tested |
| S06 | Protect tool execution against data exfiltration, unauthorized state changes and malicious fixtures | Partial | [Separate isolated SQL adapter](isolated-tools.md) in source qualification: pinned process/container boundary, scoped fields/transitions/CAS, actual final state and canonical one-shot intents. General application tools and final publication/installed acceptance remain unfinished. Product RBAC alone is insufficient. |
| S07 | Run versioned adversarial suites with attack coverage, utility and false-positive reporting | Partial | [Paired scoped-record suite](security-suites.md), accepted source in PR 47: immutable revisions, whole-family holdout, native/tool reservations, realized canary/effect checks, benign/attacked utility, unknown coverage and independent legitimate-tool-denial counts. General classifier FPR, adaptive generation, third-party benchmark integration and broader targets remain unfinished. |
| S08 | Enforce optional runtime input/output/tool policy with explicit fail-open/fail-closed semantics | Partial | [Runtime follow-up](runtime-guardrails.md), under source qualification: bounded literal/schema/tool-name rules, separate scoped checker, explicit JSON application boundaries, encrypted approvals/shared intents and guarded native JSON/SSE delivery. Final browser/installed/image/public qualification, broker interposition, additional classifiers and broader provider targets remain unfinished. |
| S09 | Keep source and private application identity outside public examples/history/archives | Bounded | `scripts/check_public_boundary.py` with external private marker policy |
| S10 | Demonstrate customer key/storage/IdP/network/deletion-backup operations in the intended deployment | External | Local/contract evidence exists; actual customer operational acceptance remains open |

## 11. Reliability, scale and collaboration — core baseline / enterprise expansion

Evidence: [Braintrust administration](https://www.braintrust.dev/docs), [Fiddler](https://docs.fiddler.ai/), [Logfire](https://pydantic.dev/docs/logfire/get-started/), [LangWatch](https://langwatch.ai/docs/introduction).

| ID | Acceptance requirement | Cutback state | Current anchor / remaining work |
|---|---|---|---|
| R01 | Run real released migrations and upgrade existing installations without rewriting history | Bounded | Real PostgreSQL upgrade/downgrade/drift contracts; released migrations 0001–0019 remain immutable. New schema work requires additive revisions. |
| R02 | Reserve experiment spend before every target/judge call; uncertain crashes never silently retry | Bounded | Durable worker ledger, locks/leases and failure tests |
| R03 | Bound body sizes, batch counts, query windows, rate limits and tenant queues | Bounded | Shared quotas and queue fairness; published capacity envelope is limited |
| R04 | Retain summaries without inventing missing raw quantiles, identities or coverage | Bounded | `server/operations.py`, retained-dimension tests |
| R05 | Restore backups and reconcile encrypted-object/database pointers after faults | Bounded | Local backup/object recovery tests; customer/cloud deployment remains external |
| R06 | Prove sustained ingest/query/worker concurrency and isolation at a declared load envelope | Partial | Owned-loopback two-tenant mixed ingest/replay/span and separate query phases have raw-sample receipts and explicit local thresholds; sustained mixed evaluator/worker/gateway and intended deployment capacity remain unqualified. [Scope](load-qualification.md). |
| R07 | Share saved views, dashboards, comments and incident ownership safely across a team | Partial | Typed project-scoped shared metadata views and audited incident ownership/history; broader shared dashboards/comments remain unfinished |
| R08 | Offer optional enterprise SSO/SCIM/provisioning with revocation and audit behavior | Partial | OIDC/PKCE and project grants exist; SCIM and real customer IdP acceptance missing |
| R09 | Publish a working accessibility/responsive dashboard for every supported lifecycle | Partial | Existing authenticated browser/axe tests pass within current views; missing journeys cannot pass |
| R10 | Provide stable API/CLI schemas, release notes, migration guidance and dependency/security updates | Bounded | OpenAPI/types, CI/release/security gates; maintain as scope grows |

## 12. Specialist extensions and outcome evidence

Evidence: [Coval](https://docs.coval.ai/welcome), [Maxim](https://www.getmaxim.ai/docs/introduction/overview), [Giskard](https://docs.giskard.ai/oss), [OpenLIT](https://github.com/openlit/openlit), [Google evaluation](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/evaluation-overview).

| ID | Acceptance requirement | Cutback state | Current anchor / remaining work |
|---|---|---|---|
| X01 | Evaluate voice latency, interruptions, turn-taking, audio quality, accents and IVR behavior | Missing | Specialty; text transcripts cannot establish voice acceptance |
| X02 | Evaluate image/audio/video outputs with native modality provenance and costs | Missing | Specialty; do not widen text-only accounting claims |
| X03 | Test stateful browser/computer agents with isolated tools and verifiable final state | Missing | Specialty; requires sandbox/side-effect contract, not arbitrary subprocess import |
| X04 | Map cloud-native evaluation results into portable versioned evidence | Missing | Bedrock/Foundry/Google adapters need real-format fixtures and optional account qualification |
| X05 | Correlate GPU/host/network/retrieval spend with a complete workflow | Missing | Specialty; existing modeled LLM costs cannot reconstruct it |
| X06 | Generate shareable independent sample evidence with reproducible data and failure cases | Partial | Independent support, agent/RAG, review, monitoring, campaign/outcome and load examples with reproducible synthetic failures exist. Broader security/specialty examples and observed first-user acceptance remain missing. |
| X07 | Show measured cost per independently successful application task with known overhead | Partial | First-class declared outcomes, trace-bound complete cost windows, failed-task spend and canonical optimization overhead are implemented. Independent outcome truth, population coverage and real customer cost-per-success acceptance remain external; [semantics](task-economics.md). |
| X08 | Show realized versus estimated benefit and payback after search/review/infrastructure overhead | Partial | Immutable change-linked before/later-window improvement receipts include declared recurring and one-time costs and keep incomplete payback unknown. Synthetic deltas do not establish real customer return or deployment causality; [workflow](campaign-outcomes.md). |
| X09 | Demonstrate a task-matched advantage against the user's incumbent and closest competitor | External | Desk research cannot establish this; use the predeclared comparison protocol |
| X10 | Demonstrate repeated customer use, accepted changes and willingness to pay | External | No adoption/revenue/fundraising evidence invented; outreach needs owner authorization |

## Release interpretation

This is 120 acceptance requirements, not 120 promised features already built. Existing bounded rows must keep passing their qualification tests while missing connections are implemented. Core baseline journeys must work end to end before a broad evaluation/monitoring launch claim. Platform and specialty work must remain visible with explicit delivery or qualified integration plans. A generic “all done” report is disallowed while these rows remain materially incomplete.

The [implementation roadmap](implementation-roadmap.md) groups dependencies into coherent releases. The [differentiation plan](differentiation.md) separately states what must be proved to justify switching to Cutback. Neither a long checklist nor a large test count proves a fundable business.
