# Search for a useful application change

This is unreleased source-checkout functionality. PyPI `0.1.0.dev5` retains the earlier single-plan workflow. Use the repository development installation (`uv sync --locked --all-extras --dev`) to test this increment; do not expect the published dev5 wheel to expose `--search`, `confirm` or `gate`.

## Test without provider costs

With the repository development environment and its local Compose PostgreSQL running:

```sh
python scripts/check.py tests/integration/test_search_workflow.py -q
```

The test creates an independent application and disposable database, uses the pinned OpenAI SDK with a synthetic HTTP transport, prepares five alternatives, runs validation, rejects a cheaper wrong answer, freezes one candidate, runs untouched test groups, checks the actual source diff/rollback, and verifies the CLI gate. Another scenario returns a truncated native response and checks a safe stop without retries. No provider key, production application or paid API is needed. This tests engineering behavior, not real model quality or savings. The test helper reads the owned Compose database secret without printing it.

The ordinary `cutback demo` shows sample quality failures and the new trade-off table in a source checkout. It continues to use synthetic inference. The table is available for ordinary worker results as well as search portfolios.

## Run an authorized application experiment

Ask the coding agent to complete [application onboarding](agent-workflow.md), including the selected collector, request-content policy, permitted examples and meaningful criteria. The commands below show the protocol; their budget numbers are examples, not authorization for real requests.

```sh
cutback optimize . --json
cutback workflow prepare --local --search --example cases.jsonl \
  --rights-basis owner_authorized --rights-reference actual-rights-review \
  --minimum 0.9 --independent-labels --max-usd 5 --max-requests 1000
```

Preparation makes no inference calls. It freezes the baseline native request and at most five variants: a qualified cheaper same-provider model, half/quarter output bounds, and combinations. Instructions, input templates and structured output contracts stay unchanged. The portfolio is generated before seeing outcomes. The existing qualified model matrix still applies; this does not add new providers or model qualifications. A workflow on the cheapest model can still test smaller explicit output bounds if its current bound exceeds 16.

The first run uses **validation groups**, sharing one worst-case dollar/request/time ceiling across baseline and all alternatives. If the full portfolio does not fit, preparation fails; it does not quietly drop candidates or authorize a smaller statistical claim. Reducing an output bound does not prove lower billed usage. Only complete modeled costs can establish a sample difference.

After reviewing and genuinely approving the exact search plan:

```sh
cutback workflow continue --plan-hash SEARCH_HASH --approval-reference actual-search-budget
# An authorized operator configures the separate worker BYOK key, then runs the worker.
cutback workflow work
cutback workflow continue
```

Review `search_results`: candidate eligibility, failure reason, minimum sample score, Decimal cost per request, mean observed latency and the nondominated sample frontier. Unknown/incomplete latency cannot establish dominance. The frontier is descriptive: selection on the validation sample does not independently verify the chosen change. Monthly/production savings remain unknown.

Select **one** passing candidate and freeze it before any test evaluation:

```sh
cutback workflow confirm --candidate SEARCH_KEY \
  --max-usd 2 --max-requests 200 --purpose-reference actual-confirmation-review
cutback workflow continue --plan-hash CONFIRMATION_HASH \
  --approval-reference actual-confirmation-budget
cutback workflow work
cutback workflow continue
```

Confirmation requires its own exact budget approval. The server binds the candidate, baseline, dataset, source, metric, criteria, evaluator and prices to the completed validation run. Test groups must be untouched. PostgreSQL serializes their reservation across concurrent plans. Once a test run is reserved, ordinary test plans cannot expose those groups again through this workflow. A failed/cancelled/uncertain confirmation does not reset this evidence boundary; obtain fresh independent data for another confirmation. A customer with access to their own dataset can inspect it outside Cutback; the software cannot establish that no external human or system saw the holdout.

Both runs use existing durable reservations and no implicit provider retry. A truncated response is never treated as a valid answer. Under the conservative current pricing contract, an incomplete native response remains potentially billable with unknown cost: execution stops and retains its reservation. Inspect that result; never repeatedly rerun it to get a favorable candidate.

## Review the change and enforce a gate

```sh
cutback workflow proposal --candidate SEARCH_KEY --purpose-reference actual-source-review
cutback workflow gate --candidate SEARCH_KEY
```

The proposal refuses validation-only evidence. It can change the bound model literal/config, or reduce a same-file literal output bound (including a combined model/cap edit). Unrelated numeric literals remain unchanged. Imported or computed output bounds and multi-file model-plus-cap changes require inspected custom work. The output includes the exact diff, reverse diff, hashes and affected-workflow coverage. Source files stay unchanged. Applying the patch changes the source hash; rerun discovery and qualification for subsequent work.

The gate performs a fresh authenticated read of the canonical report and checks current source integrity. It currently uses the original workspace's journal and unchanged pre-patch source. It is not yet a portable post-change PR/CI regression contract; that work is tracked in [the baseline](market-baseline.md). Exit 0 requires complete accounting, a matching frozen test plan, lower sample cost, declared quality gates, independent-label evidence, statistical gates and the existing heldout-verification status. Otherwise it exits 1 with safe reasons. `--sample-only` explicitly relaxes the independent-quality requirement for engineering demos; it still refuses a validation-only search, stale plan or incomplete run. Never present that option as customer-quality verification.

No gate deploys, approves a patch, certifies business success, or proves a customer saving. Application tests, additional affected workflows, real provider qualification and staged traffic remain necessary. This increment does not implement prompt generation, learned routing, application-wide autonomous fixes or a managed cloud service. The broader [research and roadmap](product-research.md) records those gaps.
