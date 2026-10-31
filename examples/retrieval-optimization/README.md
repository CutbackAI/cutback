# A real corpus retrieval application

This independent support app searches a small synthetic document corpus before calling an injected native SDK client. The current retrieval configuration finds the refund deadline. A smaller chunk can lose it. Cutback compares actual contexts, not prerecorded retrieval responses.

**No provider call is needed to explore or test this example.** The source-built `cutback demo` now prepares validation, separate confirmation and a source configuration proposal using actual SDK parsers with owned synthetic HTTP responses. Open **Retrieval** to inspect it. All answers/token counts in that demo are synthetic; CPU timings and retrieval execution are measured locally. [Exact contracts, limits and privacy](../../docs/retrieval-optimization.md).

## First, run the application offline

Install the matching source checkout in a development environment (`uv sync --locked --all-extras --dev`), then:

```sh
uv run python examples/retrieval-optimization/app.py "refund return deadline for amber"
uv run python examples/retrieval-optimization/check_application.py
```

The first command prints the actual reference frame. The second checks retrieval and calls `app.answer` through the pinned native Responses SDK with an intercepted success and outage. Both HTTP replies are synthetic; no inference endpoint is contacted and real model quality is not established. To call a real provider, supply a separately authorized client and budget. Cutback does not obtain provider credentials from this example.

## Try the complete guided workflow

```sh
uv run cutback demo
```

Choose **Explore sample**, then **Compare real corpus retrieval configurations**. Open the completed validation and confirmation in Retrieval history. Review quality failures, context bytes, CPU components and billing completeness. To read actual passages, enter a purpose in encrypted case review. Synthetic confirmation remains local review evidence. Nothing is applied or deployed.

## Prepare an independent source proposal

The source checkout also adds an [agent-driven path](../../docs/agent-retrieval.md):
`optimize` discovers this producer, and `workflow prepare --automatic` freezes
the actual corpus and questions after explicit authorization. Validation,
separate confirmation and source proposal use the same resumable workflow.
This extension is under qualification and is absent from published dev5.

`cutback demo --agent-retrieval` runs the fixed-model path with synthetic native
responses, real PostgreSQL, checks on both application trees and verified
rollback. Inspect its separate validation and confirmation in Retrieval history.

`cutback demo --joint-model-retrieval` runs the new joint model/output/retrieval
path, including native SDK success/outage checks against both committed trees.
The [joint walkthrough](../../docs/joint-retrieval.md) explains explicit corpus
authorization, exact approval, candidate selection, confirmation and combined
source patch/rollback. The manual steps below preserve the fixed-model workflow;
automatic source preparation now selects the joint engine.

For your own local synthetic trial, copy this directory outside Cutback and initialize a clean Git repository there. Keep generated requests/receipts outside that source tree. The application has no dependency on another private product.

1. In the collector, authorize encrypted storage for the `synthetic-retrieval-search` staging workflow with synthetic rights and OpenAI vendor scope. The example contains 40 fictional policy families with different 14/21/28-day deadlines, one labeled question per policy and explicit family groups. Prepare and import those questions:

   ```sh
   python prepare_dataset.py --policy POLICY_UUID --output ../questions-import.json
   cutback datasets import PROJECT_UUID ../questions-import.json --endpoint COLLECTOR
   ```

   Rephrases of one policy must keep that policy's group. Do not manufacture independence by changing numeric identifiers or words; reimports cannot refresh consumed test groups. This synthetic corpus is nonrepresentative, and its passing result is local demonstration evidence only. The source-built demo creates a working dataset automatically.
2. In the independent copy, prepare its numeric settings/source receipt:

   ```sh
   cutback retrieval inspect-config . retrieval.json --output ../inspection.json
   python prepare_request.py --dataset DATASET_UUID --output ../retrieval-request.json
   ```

3. Use the Retrieval dashboard's form and upload `corpus.json` plus `../inspection.json`. For this small synthetic example, declare the illustrative CPU assumption `0.01` USD/CPU-hour and a minimum of three questions/groups. Keep the model `gpt-4.1-mini`, 512-character chunks, zero overlap and top-k one. Confirm content authorization and prepare. Alternatively, `cutback retrieval draft ../retrieval-request.json --project PROJECT_UUID --endpoint COLLECTOR --authorize-corpus-upload --output ../validation-plan.json` uses a scoped control credential from memory/environment; it does not start inference.
4. Review and approve the exact validation budget, then queue one run on an explicitly enabled worker. **Do not enable real provider execution just to explore the sample.** The one-command demo supplies its intercepted synthetic transport. A normal worker requires separate provider/spend authorization.
5. When validation completes, choose **Prepare selected candidate confirmation**. Review and approve the separate untouched-test budget; queue it once. An uncertain request is never automatically retried. The numeric token counts used by the demo are fixture usage, not real tokenizer results.
6. After confirmation, enter a proposal purpose and download `retrieval-proposal.json`. In the exact independent base repository:

   ```sh
   cutback retrieval patch . ../retrieval-proposal.json --output ../retrieval-change.json
   ```

   This writes a forward `.patch` and `.rollback.patch`. The application stays unchanged. A changed corpus, config or source tree invalidates the proposal.
7. Create an independent proposed clone, apply the forward patch with byte-preserving Git newline settings, and commit the proposed settings. Discover and run the existing checks against both clean trees:

   ```sh
   cutback checks discover BASE_ROOT PROPOSED_ROOT --output ../checks.json --report ../check-discovery.json
   cutback checks run ../checks.json BASE_ROOT PROPOSED_ROOT --execute-local --output ../check-receipt.json --junit ../checks.xml
   ```

   These commands execute only explicitly reviewed local checks. They use a minimal environment and are not an OS sandbox. An installed source-matched Cutback is needed by the independent app.
8. Write a coverage JSON mapping the declared workflow to command keys from the discovered contract, for example `{"support-rag":["application"]}` only when `application` is the actual discovered key. Bind the diff and receipts:

   ```sh
   cutback retrieval review ../retrieval-change.json ../checks.json ../check-receipt.json BASE_ROOT PROPOSED_ROOT --coverage ../coverage.json --branch codex/retrieval-review --output ../review.json
   ```

   The local branch cannot overwrite a different ref. Synthetic evidence stays `READY_FOR_LOCAL_REVIEW`. A real release still needs representative task evidence and review; no command here pushes or deploys.

The accepted automated test executes SDK → authenticated HTTP → real PostgreSQL → actual retrieval variants → independent confirmation → exact diff → both application trees → review ref → rollback check. Browser acceptance covers authoring, approval, case access, viewer denial, mobile layout and accessibility. See [executed evidence](../../docs/progress.md), rather than infer acceptance from this README alone.
