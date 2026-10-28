# Semantic checks for agent and RAG episodes

Source-checkout feature. PyPI dev5 does not contain this addition. The ordinary PostgreSQL experiment worker evaluates each declared synthetic episode, then optionally makes **one separately reserved judge request** for its completed answer. No real application tool is executed. Planning makes no inference call.

## Try it without a provider account

```sh
uv sync --locked --all-extras --dev
uv run python examples/agent-evaluation/sample.py --semantic
uv run cutback demo
```

The command finishes with `COMPLETED`, twelve native SDK requests, baseline score `1` and intentionally wrong candidate score `0`. Eight requests execute the synthetic agent/RAG conversations and four exercise the ordinary structured text judge executor. The intercepted responses, token usage and judgments are **scripted fixtures**, not real model benchmarks or customer savings. Nothing reaches a vendor.

In the demo, choose **Explore sample**, **Inspect agent and retrieval tasks**, then **Load execution receipts**. Compare the `evaluation` and `judge` requests and their separately known charges. Read encrypted case outputs with an explicit purpose to inspect the conversation, deterministic checks, semantic proxy and raw judge response. A purpose-bound read needs a current authorized administrator and active content policy.

To prepare your own standalone comparison, open **Experiments → Plan an experiment**, select a frozen typed episode dataset and use a cheaper supported model or reviewed instruction variant. Enable **Add a semantic model judge**. Add up to three criteria, write their task-specific rubrics, select an exact supported OpenAI text model and set its input/output bounds. The proposed plan freezes the criteria, model request, nonce, prices, native turn bounds and budget. Review the exact approval hash before approving and queueing a run. An ordinary provider-key worker refuses the demo's `scripted_test_only` plans.

## What each criterion receives

| Criterion | Evidence available to the judge | What it does not establish |
|---|---|---|
| Answer correctness | Completed answer, observed user inputs and an explicitly supplied gold/reference or accepted-answer fixture | Independent label correctness, population coverage or customer task success |
| Retrieval groundedness | Completed answer, observed user inputs and document text from actual returned, integrity-checked retrieval results | Full-corpus recall, real retriever quality or proof that every claim is supported |
| Conversation constraints | Completed answer, observed user inputs, scenario system instruction and observed user/assistant turns | Real user behavior, generative personas or actual external handoff completion |

Only the requested evidence categories are included. Hidden state assertions, unreturned tool fixtures and unrequested gold/reference content are excluded. Model-visible source IDs are opaque; the response must name the exact criterion keys and quote a nonempty exact substring from that criterion's permitted observed sources. Unknown sources, invented/blank/duplicate citations, duplicate JSON keys, extra fields, nonfinite or boolean scores and missing criteria are invalid. Retrieved instructions remain untrusted data. These controls bound the evidence and catch malformed output; they cannot guarantee a judge resists every injection or interprets a quotation correctly.

The API accepts `episode_judging`, `episode_judge` and `episode_max_turns` on `POST /v1/projects/{project_id}/experiments/draft`. Direct immutable plans use `deterministic-v5` and `native-fixture-episode-v2`. A random content nonce prevents short rubric values from being tested against a public content hash. The owned judge request uses synchronous, nonstreaming OpenAI Responses with a strict response schema. Targets retain their separately qualified OpenAI Responses/Chat or Anthropic Messages contracts. An Anthropic-only policy cannot silently authorize an OpenAI judge. The [provider qualification](providers.md) and [acceptance record](progress.md) define exact supported SDKs/models; SDK qualification does not imply support for arbitrary newer models.

## Results and accounting

Semantic results are **Tier 3, NOT VERIFIED**. A valid model score at or above its frozen minimum is a proxy pass. It cannot repair an observed deterministic violation. Incomplete answers, missing references/documents, malformed judge output or invalid source quotations leave the affected semantic check unknown. A known failed check remains failed even when another check is unknown. Reports preserve counts of valid, passed and unknown assessments rather than replacing missing values with zero.

The pre-call forecast includes every native turn upper bound plus one judge per case/model/repeat, under the same total request/USD/wall envelope. Migration `0017` permits a maximum of 32 native calls plus one separate judge slot. A target cannot consume the judge slot; concurrent judge reservations cannot exceed one. Each actual call commits its scoped reservation before SDK I/O. Known billable usage survives cancellation, revocation and later invalid assessment. Missing charge coverage stops further semantic work. A lost ledger acknowledgement stops all subsequent calls; it cannot be retried as an apparently unused in-memory budget slot.

Judge spend is included in total evaluation cost and reported separately. Target latency excludes judge latency. Cost per declared fixture success distinguishes target-only spend from evaluation spend including the judge. Production cost per successful task remains unknown. Neither a successful HTTP response nor an exact source quote is independent proof of semantic quality.

Rubrics and raw judgments use the existing authorized encrypted content plane. Analytical rows, call receipts, exports and logs contain no raw rubric, answer, document text or quote. Paused/expired policies and unauthorized roles cannot reopen retained content. Receipt retention preserves cumulative request and known-cost totals; missing rows cannot reconstruct distributions. Project deletion removes both scoped metadata and encrypted evidence. A populated v5 deployment refuses downgrade until its evidence has been deliberately exported and deleted; released migrations remain unchanged. Uncertain commits preserve potentially referenced ciphertext for reconciliation.

This adds semantic assessment to declared synthetic fixtures. It does not implement generative user simulation, production tools, semantic drift models, continuous outcome follow-up or automatic deployment/rollback, and does not finish the [remaining roadmap](implementation-roadmap.md).
