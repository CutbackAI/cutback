# Learn a route, then test it independently

This independent synthetic ticket application makes the idea concrete: a small
model works on short fixture tickets, fails some longer ones, and the baseline
remains available within its approved input bound. Input size is a declared
feature, not a measure of task difficulty. The supplied `routing.json` is an
illustration, not a policy qualified for a real application.

Use a current source installation; the published PyPI dev5 wheel does not include
this workflow. Git, Python 3.11–3.13 and Docker Desktop with Linux containers are
required for the complete collector demo:

```sh
uv sync --locked --extra server --extra openai
uv run cutback demo --learned-routing
```

Choose **Explore sample**, then **Routing**. The seeded training comparison,
fitted policy, independent validation and untouched confirmation use the actual
collector, PostgreSQL, encrypted storage, native SDK parser and durable call
ledger. All provider replies and usage are synthetic. No provider key is needed.
The local worker accepts only this sample project's approved fixture routing
plans and refuses ambient credential fallback.

To repeat the journey in the dashboard:

1. Expand **Prepare a training comparison**. Select the
   `synthetic-learned-routing` dataset, OpenAI, fixture responses, `gpt-4.1` and
   `gpt-4.1-mini`. Use the instruction from `routing.json`, 8192 input tokens,
   64 output tokens, required score 1, regression 0, three confirmation samples
   and three groups. Set this stage's ceiling to USD 2 and 500 requests.
2. Freeze the plan, read its exact private specification with a purpose
   reference, approve that stage, then queue it. Refresh execution until complete.
   If the queue reply is lost, refresh; do not create another run or approval.
3. Fit the completed training run using four maximum leaves, three minimum
   cases per leaf and three training groups. Read the private fitted policy.
4. Prepare validation with a new USD 2 / 500 request ceiling. Read, approve and
   queue this separate stage. Fitting alone is not independent evidence.
5. Select that completed validation for untouched confirmation. Review and
   approve its own USD 2 / 500 request ceiling, then queue it. Read its fresh-group
   receipt and declared quality results.
6. Inspect shared training setup cost separately from target evaluation spend.
   Count its setup key once across both comparisons. Compute, hosting and
   amortized full task costs remain unknown.

These are synthetic budget ceilings, not permission to contact a vendor. The demo
rejects live-provider routing plans. Ctrl+C stops its owned worker/server and
temporary database. An unstarted blocked run can be explicitly rechecked after
correcting its worker or policy; started uncertain work cannot be replayed.

## Check the application independently

From this directory in the source environment:

```sh
uv run python app.py
uv run python check_application.py
```

The first command prints offline route metadata. The second supplies an owned
transport to the actual native SDK and checks both branches, native response
preservation, the instruction and refusal above the approved bound. Neither
contacts a provider. The scanner parses this source without running it.

For a real application, the owner must authorize its data and each native budget,
qualify the fitted policy against independent task checks, review its exact
configuration integration and affected consumers, and measure overhead and later
outcomes. This workflow does not automatically patch or deploy the application.
See [the contract and CLI](../../docs/learned-routing.md).
