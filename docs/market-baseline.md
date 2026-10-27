# Cutback's market baseline

Audit date: 27 October 2026. This replaces a feature-name checklist with user-visible acceptance requirements. The [source review](research/market-sources-2026-10-27.md) supports market availability; the paths below support Cutback's actual state. Requirements are additive to the master engineering specification and repository privacy/security rules. They do not authorize paid calls, production changes, arbitrary tool execution or publication of private applications.

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
| F02 | One independent sample demonstrates installation → observation → failing check → reviewed improvement | Partial | `examples/support_desk/`, `src/cutback/demo.py`; current supported native single-call path, not general agent journey |
| F03 | An unfamiliar developer completes the sample without maintainer intervention and explains why a candidate failed | External | Automated sample evidence exists; observed first-user trials do not |
| F04 | Existing coding agent can discover commands, prepare work and resume an interrupted workflow | Bounded | `agent.py`, `cli_agent.py`, packaged agent workflows; client discovery still needs independent acceptance |
| F05 | Setup diagnoses unsupported source/request contracts and offers a concrete next action | Partial | Request-profile rejection reasons exist; guided resolution/adapters remain narrow |
| F06 | Read an existing OTel/OpenInference producer without requiring a new model client | Bounded | `server/workflow_otel.py`, pinned OpenTelemetry 1.45.1 / OpenInference 0.1.42 producer; explicit bounded JSON metadata projection, not all exporters |
| F07 | Import a qualified existing trace export while preserving provenance and unknown fields | Missing | No qualified Langfuse/Phoenix/Braintrust export adapters |
| F08 | Import an existing evaluation suite with declared inputs/expected outputs/criteria | Partial | `evals/records.py` maps JSON/JSONL/CSV and `datasets.py` freezes native cases; no qualified external suite adapters |
| F09 | Produce a portable result consumable in another workspace or CI runner | Partial | Canonical server reports exist; local workflow journal/gate binds the original workspace/source |
| F10 | Versioned documentation tells installed-wheel users which commands are unreleased | Bounded | `docs/optimization-search.md`, release artifact validation; maintain on every release |

## 2. Trace, session and workflow investigation — core baseline

Evidence: [OpenInference](https://arize-ai.github.io/openinference/spec/), [Phoenix tracing](https://arize.com/docs/phoenix/tracing), [Langfuse sessions](https://langfuse.com/docs/observability/features/sessions), [Fiddler](https://docs.fiddler.ai/getting-started/agentic-monitoring).

| ID | Acceptance requirement | Cutback state | Current anchor / remaining work |
|---|---|---|---|
| T01 | Observe supported native LLM calls without altering native response/stream semantics | Bounded | `instrumentation/`; pinned Python SDK qualification only |
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
| O02 | Separate application traffic from evaluation, judge and optimizer overhead | Partial | Application/evaluation/judge dimensions exist; future optimizer calls must use the same accounting boundary |
| O03 | Expose exporter drops/outages and avoid presenting partial telemetry as full population coverage | Bounded | SDK health/collector reports; coverage remains unknown where not measurable |
| O04 | Define immutable/versioned online evaluation rules with sampling, scope and a budget ceiling | Missing | Experiment schedules do not constitute production evaluation rules |
| O05 | Join delayed scores/feedback to the exact span, trace or session without tenant ambiguity | Bounded | Versioned immutable external result IDs, exact project/environment/trace/span/session targets and delayed joins |
| O06 | Distinguish evaluator errors, missing data, invalid scores and actual application failures | Partial | External passed/failed/error/unknown shapes and absent results remain distinct; budgeted online evaluation execution is still Release 4 work |
| O07 | Create alert rules with windows, minimum sample size, cooldown, deduplication and recovery | Missing | Health/rollout guardrails are not an incident/alert product |
| O08 | Acknowledge/resolve an incident with owner, evidence links and an audit trail | Missing | No incident record or operator workflow |
| O09 | Deliver configured webhooks/notifications with signed payloads, retries and delivery status | Missing | External messaging is not authorized by implementation alone; provide opt-in integration |
| O10 | Diagnose drift by cohort/release and distinguish input drift from labeled quality loss | Partial | Rollout windows/slices exist; continuous distribution and outcome monitoring absent |

## 4. Datasets, provenance and review — core baseline

Evidence: [Braintrust review](https://www.braintrust.dev/docs/annotate/human-review/manage-review-work), [labels](https://www.braintrust.dev/docs/annotate/labels), [MLflow datasets](https://mlflow.org/docs/latest/genai/datasets), [Cleanlab annotators](https://docs.cleanlab.ai/v2.5.0/tutorials/multiannotator.html).

| ID | Acceptance requirement | Cutback state | Current anchor / remaining work |
|---|---|---|---|
| D01 | Create frozen dataset versions with stable case identities and import provenance | Bounded | `evals/datasets.py`, `evals/records.py`, `server/evaluation_api.py` |
| D02 | Distinguish independent gold labels, historical references, generated cases and user feedback | Bounded | Dataset attestations/evidence tiers; feedback is not automatically gold |
| D03 | Prevent duplicate/customer groups leaking between train/validation/test | Bounded | Declared group and selected duplicate detection; semantic duplicates not universally detected |
| D04 | Show split/class/stratum coverage and refuse inadequate independent evidence | Bounded | Dataset split metadata and verification criteria |
| D05 | Turn a permitted production failure into a reviewed new regression dataset version | Partial | Case correction creates new version for experiment cases; generic trace promotion missing |
| D06 | Assign review items and safely claim/release work with reviewer authorization | Missing | `server/case_evidence.py` provides admin case inspection/correction, not assignment queues |
| D07 | Collect independent blind reviews without showing earlier reviewers' answers | Missing | Requires reviewer-scoped responses and immutable votes |
| D08 | Detect disagreement and adjudicate without overwriting original judgments | Missing | No multi-review/adjudication records |
| D09 | Version rubrics and measure judge/reviewer agreement on an untouched calibration set | Missing | Randomized/blind pairwise judge execution exists, but customer calibration lifecycle does not |
| D10 | Track item lineage through import, review, correction, evaluation and deletion | Partial | Dataset/hash/policy lineage exists; full cross-tool/review/trace lineage incomplete |

## 5. Offline evaluations and regression enforcement — core baseline

Evidence: [Promptfoo assertions](https://www.promptfoo.dev/docs/configuration/expected-outputs/), [Giskard checks](https://docs.giskard.ai/oss/checks), [LangSmith evaluation](https://www.langchain.com/langsmith/evaluation), [Google evaluation](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/evaluation-overview).

| ID | Acceptance requirement | Cutback state | Current anchor / remaining work |
|---|---|---|---|
| E01 | Run deterministic text/label/structured criteria with exact evaluator versions | Bounded | `evals/metrics.py`, `metrics_extra.py`, frozen plans |
| E02 | Keep technical validity separate from semantic quality and task completion | Bounded | Evidence tiers and criteria; nonempty/schema is not correctness |
| E03 | Run approved model judges with frozen rubrics, invalid-vote handling and complete costs | Bounded | `evals/experiments.py`; calibration remains external |
| E04 | Compare paired baseline/candidate cases and slices with grouped uncertainty | Bounded | `evals/comparisons.py`, `server/case_evidence.py`; sample adequacy still matters |
| E05 | Repeat stochastic tasks and expose variation without hiding failed attempts | Bounded | Experiment repetitions and cost/completeness reporting |
| E06 | Run the application's real local regression contract against a proposed change | Missing | Native request replay is not executing and verifying the whole application's behavior |
| E07 | Accept precomputed outputs without secretly rerunning an application/model | Partial | External scalar results can be ingested without inference; qualified external application-output/suite imports remain Release 2 work |
| E08 | Produce machine-readable CI/JUnit outcomes with exact artifact/source provenance | Partial | `workflow gate` provides an exit status for same-workspace evidence; portable JUnit/PR pipeline absent |
| E09 | Compare changed and unchanged workflows when a shared config/prompt is modified | Partial | `changes.py` warns about consumers; automatic impact regression testing missing |
| E10 | Reproduce an experiment from pinned data, evaluator, native request and price snapshots | Bounded | Frozen native contract; identical stochastic outputs are not promised |

## 6. Prompt development and release — core baseline / platform expansion

Evidence: [MLflow registry](https://www.mlflow.org/genai/prompt-registry/), [Maxim optimization](https://www.getmaxim.ai/docs/prompt-engineering/prompt-optimization), [Logfire](https://pydantic.dev/docs/logfire/get-started/).

| ID | Acceptance requirement | Cutback state | Current anchor / remaining work |
|---|---|---|---|
| P01 | Author encrypted text/chat prompt versions with stable immutable identities | Partial | Frozen plan artifacts exist; prompt registry authoring/version lifecycle absent |
| P02 | Declare template variables/types and validate missing/extra variables deterministically | Partial | Qualified source request-template rendering exists; no general prompt template contract |
| P03 | Diff versions and link prompts to source, experiments and observed releases | Partial | Native request/source hashes exist; registry UI/lineage absent |
| P04 | Compare prompt/model/parameter variants in a playground under explicit total budgets | Partial | Frozen experiments exist; interactive author/compare playground absent |
| P05 | Reuse prompt components with dependency/version tracking and cycle detection | Missing | No composable prompt registry |
| P06 | Promote an exact reviewed prompt version through environment aliases with concurrency protection | Missing | No immutable prompt alias promotion record |
| P07 | Resolve a pinned prompt/alias via scoped credentials without returning unauthorized content | Missing | Must use encrypted content rights, expiry and auditable purpose, not public metadata |
| P08 | Roll back an alias and show which application releases still use an older version | Missing | Source diff rollback exists; registry rollout is a different lifecycle |
| P09 | Pin generated rubrics/test criteria before evaluation instead of changing the goal during search | Partial | Plan hashes freeze criteria; automatic rubric generation is not implemented |
| P10 | Test structured output/tool schemas alongside prompt changes, including negative cases | Partial | Text/structured request contracts exist; tool execution/replay not qualified |

## 7. RAG, tools and conversation evaluation — core baseline for agent claims

Evidence: [Ragas](https://docs.ragas.io/en/latest/concepts/metrics/available_metrics/), [DeepEval](https://documentation.confident-ai.com/concepts/metrics), [τ²-bench](https://arxiv.org/abs/2506.07982), [MLflow multi-turn](https://mlflow.org/docs/latest/genai/eval-monitor/running-evaluation/multi-turn/).

| ID | Acceptance requirement | Cutback state | Current anchor / remaining work |
|---|---|---|---|
| A01 | Represent conversation turns, tool calls/results, retrieval context and final outcome separately | Missing | Native single-call text/structured replay cannot represent the full execution |
| A02 | Score retrieval precision/recall/ranking with declared relevance judgments | Partial | `evals/metrics_extra.py` has recall/nDCG/rank agreement on declared IDs; retrieval precision and a connected RAG execution/review workflow remain missing |
| A03 | Score groundedness/citations separately from answer correctness | Partial | `span_support` checks literal quotations against input at technical tier 1; semantic groundedness/citation correctness are not implemented |
| A04 | Check required/forbidden tools, arguments, ordering and duplicate side effects | Missing | Requires trajectory contract and safe tool-event representation |
| A05 | Permit multiple valid tool paths while asserting final environment state | Missing | String/sequence equality cannot prove final task success |
| A06 | Test multi-turn memory, constraints, clarification, handoff and task completion | Missing | No native conversation executor/simulator |
| A07 | Handle maximum steps, loops, timeout, cancellation and uncertain side effects safely | Partial | Billable experiment safety exists; general agent/tool executor absent |
| A08 | Replay declared synthetic/read-only tool fixtures without running arbitrary production tools | Missing | Must add explicit isolated adapters; scanner remains parse-only |
| A09 | Generate task-aware scenarios from permitted data with human-reviewable coverage | Missing | Synthetic sample apps exist; automated scenario/test generation does not |
| A10 | Evaluate security and benign task utility in the same scenario | Missing | Product security tests do not equal an application adversarial-evaluation product |

## 8. Optimization and change automation — core product outcome

Evidence: [Opik Optimizer](https://www.comet.com/docs/opik/development/optimization-runs/overview), [Ollie](https://www.comet.com/docs/opik/ollie), [GEPA](https://github.com/gepa-ai/gepa), [DSPy](https://dspy.ai/), [Not Diamond](https://docs.notdiamond.ai/docs/what-is-model-routing).

| ID | Acceptance requirement | Cutback state | Current anchor / remaining work |
|---|---|---|---|
| C01 | Discover qualified native request/source dependencies and bind evidence to them | Bounded | `core/request_profiles.py`, scanner, encrypted plan artifacts; dynamic cases abstain |
| C02 | Search more than one model/output bound under one frozen cost/request ceiling | Bounded | `evals/search.py`, agent search/confirmation; at most five deterministic candidates |
| C03 | Generate and evaluate task-specific prompt/structured-output improvements | Missing | Custom manual variants supported; automatic generation/search not implemented |
| C04 | Optimize retrieval/chunking/reranking/tool selection under task-quality constraints | Missing | RAG/tool executors and source adapters required first |
| C05 | Select among exact cache, routing, prompt changes and training using measured workload eligibility | Partial | Mechanisms exist; unified evidence-driven strategy selection absent |
| C06 | Count generator/judge/failed trial/routing overhead in the optimization economics | Partial | Existing experiment ledger is durable; future optimizer orchestration must join it |
| C07 | Independently confirm the selected change on disjoint untouched groups | Bounded | Search validation then same-dataset test confirmation; no global proof of unseen data across reimports |
| C08 | Produce an exact source-bound diff, affected-consumer explanation and reversible rollback | Partial | Literal model and same-file output-bound reductions; prompt/framework/tool diffs absent |
| C09 | Run the application's checks and prepare a reviewable branch/PR without deploying | Missing | Diff artifacts are not a turnkey application-PR pipeline |
| C10 | Measure accepted change outcomes over later traffic and recommend rollback on regression | Partial | Operator rollout windows exist; linked actual task-outcome/savings lifecycle missing |

## 9. Gateway and performance controls — platform expansion

Evidence: [LiteLLM](https://docs.litellm.ai/docs/proxy/virtual_keys), [Portkey](https://portkey.ai/docs/product/guardrails), [Bifrost](https://github.com/maximhq/bifrost), [Helicone](https://docs.helicone.ai/getting-started/quick-start).

| ID | Acceptance requirement | Cutback state | Current anchor / remaining work |
|---|---|---|---|
| G01 | Retain customers' provider SDK/gateway while observing and proposing changes | Bounded | Core architecture; native qualification, not universal proxy compatibility |
| G02 | Enforce application key/team/model budgets across concurrent callers | Missing | Experiment reservation is implemented; application gateway enforcement is a separate capability |
| G03 | Configure retries/fallbacks/timeouts without duplicating uncertain billable or side-effecting actions | Missing | Experiments deliberately avoid automatic billable retries; no general gateway reliability layer |
| G04 | Run exact scoped encrypted caching with expiry and explicit eligibility | Bounded | `server/cache_api.py`; does not imply universal automatic cache selection |
| G05 | Qualify semantic caching against false hits, privacy boundaries and task regressions | Missing | Exact cache must not be rebranded semantic |
| G06 | Compare learned routing with baseline/heuristic routing including router costs and quality | Partial | Byte-length route mechanism exists; trained/custom-router acceptance absent |
| G07 | Measure streaming first-token latency/cancellation and account for partial usage | Partial | Qualified native stream tests exist; cross-provider gateway behavior not qualified |
| G08 | Batch/schedule eligible work with measured turnaround and full cost attribution | Missing | Experiment queue is not a provider batch-optimization product |
| G09 | Evaluate context compression/token reduction against original task behavior | Missing | Smaller output caps do not implement input compression |
| G10 | Measure local-model/GPU costs without assuming public API rates equal hardware economics | Partial | Bounded local training/compute assumptions; live GPU/infrastructure metering absent |

## 10. Privacy, security and governance — core baseline

Evidence: [OpenInference privacy](https://arize-ai.github.io/openinference/spec/), [Invariant](https://github.com/invariantlabs-ai/invariant), [NeMo](https://docs.nvidia.com/nemo/guardrails/about-nemo-guardrails-library/overview), [Promptfoo](https://www.promptfoo.dev/docs/red-team/).

| ID | Acceptance requirement | Cutback state | Current anchor / remaining work |
|---|---|---|---|
| S01 | Metadata by default; arbitrary prompts/outputs/headers/exception text never silently enter the collector | Bounded | Strict event/profile schemas and privacy tests; maintain for new span/eval types |
| S02 | Store permitted content encrypted with rights, purpose, scope, expiry and quotas | Bounded | `content_policy.py`, `evals/artifacts.py`, capture/import policy |
| S03 | Enforce tenant/environment/RBAC scope on every query/mutation/content read | Bounded | Server scope guards and PostgreSQL tenant tests; new endpoints require the same |
| S04 | Rotate/revoke/expire credentials; browser tokens never persist in URLs/storage/logs | Bounded | Access keys, OIDC/PKCE, authenticated browser checks |
| S05 | Export/delete all new records and content with auditable receipts and retention | Bounded | `privacy_api.py`, maintenance; new tables must be added and tested |
| S06 | Protect tool execution against data exfiltration, unauthorized state changes and malicious fixtures | Missing | Requires separate application/tool execution boundary; product RBAC alone is insufficient |
| S07 | Run versioned adversarial suites with attack coverage, utility and false-positive reporting | Missing | No qualified red-team integration/product yet |
| S08 | Enforce optional runtime input/output/tool policy with explicit fail-open/fail-closed semantics | Missing | Capture redaction/content policy is not runtime application guardrailing |
| S09 | Keep source and private application identity outside public examples/history/archives | Bounded | `scripts/check_public_boundary.py` with external private marker policy |
| S10 | Demonstrate customer key/storage/IdP/network/deletion-backup operations in the intended deployment | External | Local/contract evidence exists; actual customer operational acceptance remains open |

## 11. Reliability, scale and collaboration — core baseline / enterprise expansion

Evidence: [Braintrust administration](https://www.braintrust.dev/docs), [Fiddler](https://docs.fiddler.ai/), [Logfire](https://pydantic.dev/docs/logfire/get-started/), [LangWatch](https://langwatch.ai/docs/introduction).

| ID | Acceptance requirement | Cutback state | Current anchor / remaining work |
|---|---|---|---|
| R01 | Run real released migrations and upgrade existing installations without rewriting history | Bounded | `server/migrations/`, migration contracts; preserve 0001–0006 |
| R02 | Reserve experiment spend before every target/judge call; uncertain crashes never silently retry | Bounded | Durable worker ledger, locks/leases and failure tests |
| R03 | Bound body sizes, batch counts, query windows, rate limits and tenant queues | Bounded | Shared quotas and queue fairness; published capacity envelope is limited |
| R04 | Retain summaries without inventing missing raw quantiles, identities or coverage | Bounded | `server/operations.py`, retained-dimension tests |
| R05 | Restore backups and reconcile encrypted-object/database pointers after faults | Bounded | Local backup/object recovery tests; customer/cloud deployment remains external |
| R06 | Prove sustained ingest/query/worker concurrency and isolation at a declared load envelope | Partial | Bounded retention/load smoke exists; mature capacity/SLO qualification absent |
| R07 | Share saved views, dashboards, comments and incident ownership safely across a team | Missing | Team identity exists; collaboration workflows largely absent |
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
| X06 | Generate shareable independent sample evidence with reproducible data and failure cases | Partial | Support sample is reproducible; broader agent/RAG/security samples missing |
| X07 | Show measured cost per independently successful application task with known overhead | Missing | Requires first-class task outcomes, numerator/denominator completeness and cost attribution |
| X08 | Show realized versus estimated benefit and payback after search/review/infrastructure overhead | Missing | Current sample deltas do not establish actual customer return |
| X09 | Demonstrate a task-matched advantage against the user's incumbent and closest competitor | External | Desk research cannot establish this; use the predeclared comparison protocol |
| X10 | Demonstrate repeated customer use, accepted changes and willingness to pay | External | No adoption/revenue/fundraising evidence invented; outreach needs owner authorization |

## Release interpretation

This is 120 acceptance requirements, not 120 promised features already built. Existing bounded rows must keep passing their qualification tests while missing connections are implemented. Core baseline journeys must work end to end before a broad evaluation/monitoring launch claim. Platform and specialty work must remain visible with explicit delivery or qualified integration plans. A generic “all done” report is disallowed while these rows remain materially incomplete.

The [implementation roadmap](implementation-roadmap.md) groups dependencies into coherent releases. The [differentiation plan](differentiation.md) separately states what must be proved to justify switching to Cutback. Neither a long checklist nor a large test count proves a fundable business.
