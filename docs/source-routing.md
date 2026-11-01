# From a trained policy to a checked application change

The working-source [routing program](routing-programs.md) puts native controls,
conditional training and untouched confirmation under one approved envelope. Its
[independent example](../examples/routing-program/README.md) also demonstrates
skipping training and stopping on unknown billing. The separately approved stage
workflow below remains available; do not approve its training template separately
after the saved agent job has selected a program.

This source feature is under qualification. Published PyPI `0.1.0.dev5` does not
contain it. Use the current source environment and follow [progress](progress.md)
for accepted artifacts. The [independent ticket application](../examples/source-routing/README.md)
contains no private application dependency.

## Try the complete independent example

From the Cutback checkout, with Git and Docker Desktop/Linux containers available:

```sh
uv sync --locked --extra server --extra openai
uv run cutback demo --source-routing
```

Choose **Explore sample**, then **Routing**. The sample application agent prepares
the source-bound training comparison, fits from its actual native call receipts,
prepares validation, and freezes one untouched confirmation. The demo authorizes
its three synthetic stages internally; every provider reply is intercepted. No
provider key or paid request is used. The original and proposed application
consumers then execute with the actual SDK against offline transports. The demo
checks byte-exact rollback and creates only a local review branch.

Review the files under `.cutback/support-desk-demo/`:

| File or folder | What to inspect |
|---|---|
| `source-routing/` | Original independent clean Git application, labels and ignored agent state |
| `source-routing-proposed/` | Exact proposed configuration and local `cutback/source-routing-review` ref |
| `routing-inspection.json` | Bound producer/configuration hashes and baseline |
| `routing-source-change.json` | Current collector proposal, forward diff and rollback |
| `routing-source-checks.json` | Actual commands and results on both frozen trees |
| `routing-source-review.json` | Exact review scope, consumer coverage and unknown economics |

Ctrl+C stops the owned demo services and removes their temporary database. Review
files remain local. Synthetic replies demonstrate the workflow and native
contract; they do not measure model quality or customer savings.

## In an authorized application

1. Preserve the application's SDK pin and existing behavior. Discovery qualifies
   only the complete supplied native wrapper and its one-baseline `routing.json`.
   A different existing application needs an inspected refactor and its own checks
   first. Discovery never imports it or guesses a dynamic request contract.
2. Run `cutback optimize . --json`. The job discovers the qualified producer,
   source/configuration hashes and candidate fixture paths without reading labels
   or making a provider call. Keep `.cutback/` ignored.
3. With existing data/store authorization and an approved collector, prepare:

   ```sh
   cutback workflow prepare . --learned-routing --local \
     --example tickets.jsonl --rights-basis owner_authorized \
     --rights-reference OWNER_REF --independent-labels --minimum 1 \
     --min-test-samples 30 --max-usd STAGE_CEILING --max-requests REQUEST_CEILING
   ```

   Use `--endpoint` and `--project` instead of `--local` for an approved remote
   collector. Supply actual permitted labels and owner thresholds. Preparation
   freezes bounded catalog alternatives and the exact source frame, imports only
   the named examples into the encrypted store, and makes no provider call. The
   stage ceiling is reused as a preparation limit; each new stage still needs its
   own exact approval. The three stages can each consume that ceiling.
4. Inspect the private plan in the dashboard or purpose-scoped plan read. Reuse
   authorization covering its exact hash and budget, then run
   `cutback workflow continue . --plan-hash HASH --approval-reference REF`.
   A configured worker or explicitly authorized `cutback workflow work .` executes
   that saved run. Continue without approval arguments to reconcile it. Once
   training completes, the agent fits and prepares validation automatically; once
   validation passes it prepares confirmation automatically. Review and approve
   each new exact hash. Lost queue replies never cause another inference run.
5. After passing untouched confirmation, run
   `cutback workflow proposal . --purpose-reference REVIEW_REF --output PRIVATE_CHANGE.json`.
   Select a protected output **outside the application tree**. The command
   rechecks current collector permission and exact source, returning a reversible
   configuration diff. It does not alter the application.
6. Prepare independent clean base/proposed Git workspaces, apply the exact diff to
   the proposed tree and commit it locally. Run the declared consumer checks on
   both through `cutback checks`. Then use `cutback routing source-review --help`
   to provide the artifact, check contract, receipt, both roots, declared consumer
   coverage, scoped fit/project/endpoint, purpose and private output. It refreshes
   collector authority, rejects unrelated changes and optionally creates a local
   `--branch`. Review the resulting evidence before a PR or production decision.

The dashboard can also import `cutback routing inspect-source APP app.py --output
PRIVATE.json`, freeze a source-bound comparison, and purpose-read/download its
confirmed proposal. Uploaded inspection and private proposal remain in memory;
clear controls, project/role changes and navigation release that state. Viewer
roles cannot read this content. The server still verifies every contract.

## Exact limits and evidence

The producer accepts a native synchronous `OpenAI` client using the official
endpoint and pinned SDK 2.29.0 or separately qualified 3.35.1. Only plain-text,
non-streaming Responses is supported. The caller owns SDK retries; Cutback adds
none and keeps a selected target fixed across those retries. Native response,
refusal, incomplete status, timeout and outage semantics are preserved. Async,
streaming, framework wrappers and custom gateways need separate adapters.

The source-bound dataset read is capped at 1,024 cases and 16 MiB of decrypted
content, checked both at plan preparation and execution. Training is bounded to
256 cases, five native targets, eight leaves and the
existing fitter's read/compute limits. Equal UTF-8 sizes choose the same target.
Source fitting never widens the original application's input domain, truncates
an input or silently drops an unsupported example. Unknown costs stay unknown.
Only current complete paired training evidence may fit a policy; validation and
confirmation retain original thresholds and source bindings.

The proposal compares recorded native components. Shared training setup is counted
once; fitting compute, hosting, amortization and full-task economics are unknown.
Passing sample gates does not establish representative customer quality. Source
checks have owner-declared consumer coverage and local execution provenance, not
independent third-party authenticity. General routing, automatic cross-engine
selection and production rollout remain separate work.
