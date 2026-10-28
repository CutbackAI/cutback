# Agent, retrieval and state evaluation

Source-checkout feature, under Release 5 qualification. PyPI dev5 does not include this workflow. This extends the PostgreSQL experiment worker; it does not execute an application's real tools in the scanner or collector.

## Try the complete journey

From a source checkout with Python 3.11–3.13 and Docker Desktop running:

```sh
uv sync --locked --all-extras --dev
uv run cutback demo
```

Open the displayed loopback dashboard and choose **Explore sample → Inspect agent and retrieval tasks**. The demo uses a temporary real PostgreSQL database, authenticated HTTP APIs, encrypted content storage, the durable worker and the pinned native SDK. Every provider HTTP response is intercepted by an explicit scripted transport. No provider key or paid request is needed.

1. Compare the baseline with `intentionally-wrong`. The support task asserts the final ticket state as well as the answer. The retrieval task separately checks declared relevance and literal citations.
2. Choose **Load execution receipts**. Inspect eight separate reservations and four task episodes. A model's intermediate tool call is its own request, not a free hidden step inside another reservation.
3. Enter a purpose under case evidence and choose **Read authorized case outputs**. Inspect the conversation, tool arguments/results, retrieval projections and final synthetic state. Metadata-only readers cannot read this content.
4. Expand **Review or revise this episode contract**. Edit the typed assertions or fixtures, enter a correction reference and create a new dataset version. The original plan, results and approval stay frozen. Generated or revised assertions do not become independent gold.
5. Stop the sample with Ctrl+C. Only its owned temporary database is removed.

For the two applications without Docker or a dashboard:

```sh
uv run python examples/agent-evaluation/sample.py
```

The baseline passes the declared synthetic contracts; the deliberately wrong answers fail. These are scripted outcomes and scripted token counts. Their modeled prices do not measure a real model's quality, latency, savings or customer business outcomes.

## Contracts and execution

A dataset case can include a `cutback.episode.v1` scenario. It freezes system instructions, scripted user follow-ups, synthetic initial state, tool definitions and exact argument/precondition/result fixtures. Assertions and relevance judgments remain evaluation-only. The native model sees tool definitions and observed conversation/results; it does not receive fixture answers, reference answers, expected final state or relevance labels in advance.

`episode_execution` explicitly selects the native fixture runner, maximum turns/tool calls, OpenAI surface and transport contract. It requires `deterministic-v4` and `episode_contract`; legacy single-call plans stay `deterministic-v3`. New optional fields are omitted from legacy serialization, preserving old dataset and approval hashes. A typed episode dataset cannot silently run as a single text prompt. Planning and draft generation make zero model calls.

The qualified native executor uses OpenAI **2.29.0** Responses or Chat Completions and Anthropic **2.3.0** Messages. It supports synchronous, nonstreaming text and one native function call per turn. Parallel calls, built-in provider tools, multimodal content, arbitrary SDK options, custom provider URLs, routed agents and unqualified response shapes are refused or remain unknown. This does not expand the observer's SDK/async/stream matrix. Structured final responses contain an answer and explicit document/quotation citations.

Only the declared synthetic state runner handles tool calls. It performs no network, file, shell, browser or application-code execution. Tool arguments must match the bounded schema and an exact declared fixture. State updates are staged atomically and require declared effect identities. A repeated state-changing operation is recorded as a duplicate attempt and does not apply again, including when its original precondition has changed. Unknown tools and undeclared arguments are denied.

The scenario permits several valid tool paths while asserting final state separately. Conversation constraints can check exact accepted responses for declared user turns. Scripted follow-ups test memory/clarification/handoff contracts; they are a frozen simulation, not a measured distribution of real users or a generative user simulator. A handoff can be modeled as an explicit fixture/state transition.

Limits apply to JSON depth, nodes, byte size, messages, tools, fixtures, response size, episode turns, tool attempts, project receipts and native requests. There are no implicit model retries, tool retries or fallback providers. The wall deadline prevents subsequent calls; an already dispatched provider operation may outlive a transport timeout or finish late. It cannot be recalled or safely repeated merely because its response was lost.

## Accounting and failure semantics

Migration `0013` adds content-free episode and call receipts, project/environment/run identities and cumulative known-cost coverage. It preserves released migrations. A durable transaction locks the project/run/job, checks the exact approved plan hash, current approving principal, current capture/provider rights, lease, per-episode bound and shared request/USD/quota caps, then commits a unique call reservation **before SDK I/O**.

Each response records known usage separately. A fully received text response can have known billable usage even when its answer is refused, truncated or invalid; task validity is graded separately. Missing or unsupported usage remains unknown. A request that never started can have a known zero charge; a lost response is not zero. SDK payload logging is suppressed only within owned worker requests.

Cancellation and revocation stop further turns and fixture execution. Late responses may repair known cost but cannot revive cancelled/expired tasks or authorize a change. Expired charged calls become uncertain and are never automatically replayed. Deletion refuses a live reserved call even if its task has been cancelled. Per-call rows and completed episodes have retention/export/delete paths; cumulative reserved requests, known-cost request counts and known subtotals survive receipt pruning. Legacy cost coverage remains unknown. Empty retained pages cannot reconstruct missing quantiles or coverage.

An outage, cancellation, budget stop, missing usage or invalid evidence can leave checks unknown. Missing coverage does not become an application failure or success. Observed violations remain visible. Reports distinguish assessment states, task outcomes, declared check failures, native request counts and known-cost coverage. Full production cost per successful task remains unknown; fixture model cost per declared success is a narrower quantity. Synthetic reports do not automatically produce a staging/deployment recommendation.

## Retrieval and citations

Retrieval events are projected from actual returned fixture documents, preserving IDs and optional backend scores. Backend scores are not reference grades. Precision, recall and nDCG require declared relevance judgments; unjudged returned documents preserve missing metric coverage. Recall is relative to the declared references, not an unseen entire corpus. Literal citation support checks exact quotations in actually retrieved text. It does not establish semantic groundedness, answer correctness or general prompt-injection resistance.

## Reviewable scenario generation

```sh
uv run cutback episodes generate owned-episodes.json --output scenario-draft.json
uv run cutback episodes assess scenario.json observation.json
```

The generator accepts bounded owned synthetic `DatasetImport` content. It creates source anchors, follow-up/repetition cases, unauthorized-tool injections and, where available, retrieved-instruction injections. It makes no model calls and invents no new reference answers. Its draft includes a source hash, generator version, coverage mapping, skipped variants and review requirements. Inspect `dataset` before import. All variants share lineage with their original anchors, preventing simple transformation from manufacturing new untouched test data. Independence and representativeness are false.

These cases exercise declared utility and tool boundaries together. They complement the qualified external evaluation import workflow; they do not replace a specialized red-team framework or prove general application security.

## API and remaining boundaries

Existing dataset import, plan/approval/queue, cancellation and purpose-bound case APIs support the episode workflow. `/experiments/{run_id}/calls` uses a sequence cursor; `/experiments/{run_id}/episodes` uses an episode cursor. Both enforce project roles, bounded pages and content-free responses. Case correction accepts an episode revision instead of changing an unused scalar gold label.

Live generative simulators, real application tools, browser/computer sandboxes, voice/audio/video execution and cloud-native specialty adapters require separate contracts and qualification. They are not claimed here. Release 6 optimization automation and Release 7 platform/launch work remain separate roadmap requirements. See [progress](progress.md) for acceptance and publication evidence.
