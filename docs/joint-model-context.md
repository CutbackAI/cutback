# Joint model and context search

Working source adds `deterministic-v12`, a distinct frozen execution contract for native model choice, output ceiling and extractive context settings. It uses the existing PostgreSQL experiment approvals and call ledger. Legacy compression v7 and isolated tool/security v8–v11 retain their contracts. This feature is not in the published PyPI dev5 package. Final installed, image, public and hosted acceptance must be recorded in [progress](progress.md) before launch claims.

Start with the [independent policy application](../examples/model-context-search/README.md) or `cutback demo --joint-model-context`. The demo uses actual native SDK serialization and HTTP/PostgreSQL boundaries with an owned synthetic response transport. It cannot establish real model quality, tokenizer savings, invoice savings or a fundraising claim.

## What the agent does

`workflow analyze` inspects up to 200 tracked Python files in a clean source snapshot for the qualified context wrapper, separately from its ordinary request profiles. It records omitted-file counts and never imports application code. Qualification matches the entire bounded module AST, allowing only its literal model and output cap to vary. A different builder, added executable code, storage option or dynamic model causes abstention. The source wrapper's externally supplied SDK client's destination, retry behavior and other callers remain outside this qualification.

`workflow prepare --joint-context` rechecks the exact source, reads only explicitly permitted labeled query/context files, validates data rights, uses task-aware grouped splits and freezes up to five candidates before any inference. The deterministic portfolio includes model-only, context-only and joint alternatives where qualified. The baseline uses the same framing and original context contract. Every candidate preserves provider, instruction and native request configuration; output and context bounds may narrow. No proposer model is called. A model/output candidate is not assumed to preserve task quality.

The preflight exposes total request and conservative native cost bounds. Execution requires exact plan-hash approval. Every request reserves durable budget before native I/O, with separate preprocessing CPU measurements. Unknown native usage, uncertain dispatch or missing CPU cost basis prevents automatic economic selection. An uncertain request retains its reservation and stops without automatic retry or further joint requests. CPU pricing is owner-declared; infrastructure, recovery and full business-task costs remain unknown.

After validation, the agent selects one eligible frozen model/settings pair and freezes untouched test confirmation with a separate budget and exact approval. Current tenant, role, encrypted-content policy, group lineage and concurrent holdout reservations are enforced through the ordinary experiment workflow. Reimporting examples or repeating a confirmation does not create fresh independent evidence. Validation cannot authorize a source change.

`workflow proposal` reads a currently authorized collector proposal and prepares one exact combined change. Both the qualified producer and adjacent `compression.json` bind the original clean commit, tree and file hashes. The patch changes only literal model/output arguments and the selected settings; it supplies a byte-exact rollback. Source changes, unrelated proposed edits or tampered artifacts refuse review. Application checks must run on both exact trees in explicit local/CI workspaces. Declared consumer coverage and local receipt trust remain visible; the review never pushes or deploys.

## API and CLI

| Operation | Scoped endpoint or command |
|---|---|
| Inspect source offline | `cutback model-context inspect-producer APP_ROOT app.py --output inspection.json` |
| Draft without inference | `POST /v1/projects/{project}/model-context/draft` |
| Select and freeze confirmation | `POST /v1/projects/{project}/model-context/confirmation` |
| Read current held-out source proposal | `POST /v1/projects/{project}/model-context/{run}/proposal` |
| Prepare local diff and rollback | `cutback model-context patch APP_ROOT proposal.json --output change.json` |
| Review both-tree check receipt | `cutback model-context review CHANGE CONTRACT RECEIPT BASE PROPOSED --coverage coverage.json --output review.json` |

Draft/confirmation require analyst access; proposal reads require admin and an audited purpose. Preparing, downloading or inspecting artifacts makes no provider call. The **Compression** dashboard offers the joint-search checkbox, exact producer inspection upload, shared budget review, same-run reconciliation, changed axes, paired encrypted evidence and independent confirmation. Browser control credentials remain in memory.

The ordinary compression confirmation/proposal routes cannot turn v12 results into a settings-only patch. The v12 report explicitly sets the legacy `compression_automation_eligible` field false. Its separate `joint_model_context_confirmation_eligible` field requires native usage, declared CPU component cost and quality evidence; a smaller context or model name alone is insufficient.

## Market baseline and remaining scope

Offline benchmarks, regression checks, online evaluation and feedback into curated datasets are standard capabilities documented by [LangSmith](https://docs.langchain.com/langsmith/evaluation-types). Quality/cost/latency comparisons and evaluation-driven routing are also established; Braintrust describes comparing candidates on the same examples and rejecting hard quality failures before forming a frontier. [Primary source](https://www.braintrust.dev/articles/evaluation-driven-model-routing-pareto-frontier).

Cutback's engineering objective here is a reviewable application change joining independently checked model and context choices, bounded consent/spend and untouched confirmation. That is a product direction to validate, not a claim of market novelty or superiority. Arbitrary frameworks, learned/generative compression, learned routing, remote/vector retrieval, universal cross-engine campaigns and real customer outcomes remain separate work in the [implementation roadmap](implementation-roadmap.md).
