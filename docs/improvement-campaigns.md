# Automatic improvement campaigns

Locally accepted source-checkout functionality; hosted/publication gates remain in the progress record. The published PyPI dev5 wheel does not contain these commands. See [executed acceptance and publication state](progress.md). This is a bounded native text workflow; it does not complete every optimizer or the platform roadmap.

Start with the [independent runnable classifier](../examples/improvement-campaign/README.md), or ask a coding agent in an authorized application workspace:

> Use Cutback to prepare a source-bound improvement campaign. Discover supported workflows and permitted examples, preserve the request/output contract, compare the unchanged reference and simple downgrade against generated instruction/output-cap variants, and choose search effort from complete task costs and my conservative opportunity assumptions. Show the exact total budget before any paid execution. Resume the same campaign after interruptions. Confirm one frozen winner on untouched groups, prepare its exact diff and rollback, run my authorized application checks for every affected consumer and prepare a local review branch. Do not deploy, publish or invent business results.

## Prepare without inference

Run `cutback optimize . --json`, then [prepare the selected source-bound workflow and permitted examples](agent-workflow.md). A campaign uses that saved preparation; it does not require executing the earlier static experiment first. Metadata observation and task-outcome declarations produce [complete task-cost snapshots](task-economics.md). The snapshot must match the same environment/workflow/service/release. Native LLM costs alone cannot stand in for unknown tool or other required task costs.

The agent may locate an existing matching snapshot through the scoped API. Future volume and conservative savings per successful task remain owner assumptions. Missing/incomplete economics abstain without provider calls; the agent must not fabricate these inputs.

```sh
cutback campaign prepare --baseline-snapshot SNAPSHOT_UUID \
  --max-usd 2 --max-requests 500 --rounds 2 --variants-per-round 2 \
  --savings-per-success 0.001 --forecast-successful-tasks 10000
cutback campaign status
```

These illustrative amounts are not paid authorization. Preparation freezes the dataset, source/request contract, literal instruction edit when qualified, generator version, eligible providers/models, allowed instruction/output bounds and cumulative request/dollar/wall-time ceilings. The compiler includes generation, reference/static/generated validation, judging and worst-case confirmation. It narrows effort to fit the conservative opportunity and request caps. It also budgets proposer input capacity for all permitted prior candidate versions and aggregate feedback; it never silently widens the approved input contract. Unknown or insufficient opportunity abstains.

Current generation supports pinned native OpenAI Responses structured output. Targets support the qualified OpenAI Responses and Anthropic Messages text profiles. Dynamic tools, streams, arbitrary optimizer callbacks, cross-provider target migrations and unqualified request transformations are not included. Exact same-file literal instruction replacement/insertion is the first source-edit adapter. Imported model configuration, dynamic instructions and other source/registry layouts need separate qualification; evaluation evidence alone cannot authorize a guessed edit.

## Approve once and resume

After actual owner approval of data rights, vendors and the displayed paid ceilings:

```sh
cutback campaign continue --campaign-hash EXACT_HASH --approval-reference actual-owner-review
cutback campaign continue
```

The configured operator worker performs the journey. For an owned local stack, `cutback campaign work` advances one step and executes at most the eligible child operation using the explicitly configured worker keys. It uses real providers when configured: the synthetic sample is a separate injected transport, never a fallback to a real key. Cancellation uses `cutback campaign cancel`.

Every physical generation/target/judge request reserves against both the exact child plan and cumulative campaign in the canonical PostgreSQL transaction. There is no budget reset between rounds. Repeated continuations reconcile the same identity. Expired/crashed leases fail uncertain billable work without automatic replay, including when inference is disabled. Cancellation stops new calls; later known usage repairs accounting without restoring authority.

The proposer sees authorized training examples, prior generated configurations and typed aggregate validation feedback. It never receives held-out inputs, labels or raw validation outputs. Versions are encrypted and frozen before evaluation. Repeated configurations retain their version/disposition but skip duplicate evaluation. The worker freezes one validation winner and consumes untouched confirmation once. Cheap-but-worse, failed confirmation, incomplete accounting and stale lineage block a usable proposal.

## Prepare the exact change and application checks

```sh
cutback campaign proposal --purpose-reference actual-change-review
```

This performs an audited current-policy content read, revalidates confirmation/lineage and local source hashes, and returns the exact source diff and rollback. It changes no application file. `--sample-only` is available for owned fixtures; it keeps the evidence explicitly sample-only and cannot establish independent/customer quality.

The coding agent reviews the diff and prepares **two independent clean Git checkouts**, one at the original source and one with only the proposed evaluated source change committed. It freezes and explicitly runs [portable application checks](application-checks.md) under the application's execution authorization. Commands execute locally with a minimal environment and bounded processes, not in the collector. They are not an OS/network sandbox. Keep credentials and production access out of that worker.

Provide a private JSON coverage mapping from every returned `affected_workflows` identity to existing command keys, for example `{"app.py:classify":["classifier-contract"]}` using the actual identities. The mapping is owner-declared coverage; a passing command is not independent proof of semantic coverage. Scanner warnings or unresolved consumers require separate qualification rather than a false all-clear.

```sh
cutback campaign review --purpose-reference actual-application-review \
  --contract PRIVATE_CONTRACT_JSON --receipt PRIVATE_RECEIPT_JSON \
  --base-root BASE_CHECKOUT --proposed-root PROPOSED_CHECKOUT \
  --workflow-coverage PRIVATE_COVERAGE_JSON \
  --local-review-branch cutback/reviewed-improvement
```

Review refreshes current confirmed evidence, verifies the receipt and both current source trees, matches the exact before/after source hashes, rejects extra file changes and requires every detected affected consumer's declared checks to have passed in both phases. The optional branch creates a local ref at the exact checked proposed commit. An existing different ref is never overwritten. No push, PR submission or deployment occurs. A coding agent can then prepare the PR description from the evidence, scope, application checks, full optimization overhead and rollback; remote publication remains within the application's separately granted authorization.

Receipts contain hashes/metadata, not instructions, provider keys or application test output. Local receipts are not cryptographic third-party attestations. Protect authorized diff/content output under the application's private controls. Retention of individual costs cannot reconstruct missing campaign context or unknown overhead. Real task success, model availability, future payback and customer savings need separately authorized representative evidence.
