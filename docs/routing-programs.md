# One approved routing program

Working-source feature under qualification; published PyPI `0.1.0.dev5` does not
contain it. Use the matching source collector, agent and schema `0033`. Exact
acceptance and publication state belong in [progress](progress.md).

A source-bound routing program evaluates simple native alternatives first. If
one preserves the required quality at lower observed modeled native cost, the
program skips training and confirms that alternative. Otherwise it uses the
reserved train-only comparison, fits a bounded input-size router, validates it,
freezes one eligible choice and confirms once on untouched test families.

One approval covers the worst-case native effort of **both** trial branches and
the full confirmation. The compiler can shorten the deterministic candidate
prefix; it cannot reduce the test sample or weaken quality requirements to fit.
Failed trials, training and confirmation remain in cumulative accounting.

This is a bounded synchronous OpenAI Responses source-wrapper workflow, using
the existing v3 native and v16 source-routing contracts. It is not a general
router, cross-engine optimizer, fine-tuning service or production deployment.
The independent [walkthrough](../examples/routing-program/README.md) needs no
provider key and uses actual SDK parsing over closed synthetic transports.

## Prepare and approve

1. Follow [source preparation](source-routing.md) through its unapproved training
   template. Keep the application's source/configuration unchanged. The scanner
   stays parse-only; preparation reads only named permitted examples and stores
   payloads in the explicitly authorized encrypted store. Do not separately
   approve or execute that template when using a program.
2. Record a scoped [task-cost snapshot](task-economics.md). It must describe one
   workflow/service/release and have complete cost per successful task under its
   explicit declaration. Supply your conservative savings and volume assumptions;
   these are inputs, not observations of future return.
3. Prepare the whole program:

   ```sh
   cutback routing program prepare --target . --baseline-snapshot SNAPSHOT_UUID \
     --max-usd TOTAL_CEILING --max-requests TOTAL_REQUESTS \
     --max-wall-seconds TOTAL_SECONDS \
     --savings-per-success CONSERVATIVE_USD --forecast-successful-tasks VOLUME
   ```

   Defaults grant no execution. Unknown opportunity, missing label declarations
   or an insufficient complete envelope cause abstention. The approval cap is
   bounded by the owner's ceiling and a fraction of the declared opportunity.
4. In **Routing**, open the program. Read its private instructions with a purpose
   reference, inspect requirements, candidates, full effort allocation and exact
   hash, then approve once. Equivalently, after reviewing its private plan:

   ```sh
   cutback routing program continue --target . --program-hash EXACT_HASH \
     --approval-reference OWNER_REFERENCE
   ```

   The one wall-clock deadline starts at approval and includes queue time,
   trial execution and fitting. Worker execution remains disabled by default.
5. Run or observe the same approved program:

   ```sh
   cutback routing program run --target .
   # Explicit owned local worker, using separately configured worker credentials:
   cutback routing program run --target . --local-worker
   cutback routing program status --target .
   ```

   Remote mode needs Cutback plus HTTPX, not provider/server/database dependencies.
   It only polls a configured worker. Local mode uses the canonical worker, with
   at most four child invocations and the existing whole-program deadline. Neither
   mode grants approval, resets a budget or replays an uncertain billed operation.

The ignored `.cutback/agent/routing-program.json` freezes preparation identity,
source, project, destination and approval hash. It contains no provider/control
credential. A lost preparation reply reuses the same request; a lost approval
reply reads the same parent. An unresolved dispatched approval stops for
reconciliation. The ordinary stage commands refuse to execute the old training
template after this program takes ownership of the saved agent journey.

## Check and review the change

After successful untouched confirmation, prepare the private handoff:

```sh
cutback routing program finish --target . --workspace ../private-routing-review \
  --purpose-reference REVIEW_REF
```

This refreshes collector authority and creates independent base/proposed Git
workspaces, preserving the exact source commit and raw bytes. It exports only
tracked ordinary files and the base commit/tree; ignored data, repository config,
hooks and ancestor objects are not copied. Known tracked credential filenames
cause refusal rather than silently dropping part of the checked source tree.
This filename check is not a general secret scanner.

The default preview executes no application checks. It discovers supported
existing checks, writes `contract.json` and `discovery.json`, and checks the actual
inverse/forward patch in the private proposed copy. Review those commands and
their bounds. Alternatively pass `--commands COMMANDS.json` on both invocations
to use an explicit [application-check contract](application-checks.md).

Create `CONSUMERS.json` mapping each owner-declared policy consumer to the
returned command keys. This is an explicit coverage assertion, not automatic
proof that every consumer is exercised. Then resume the same workspace:

```sh
cutback routing program finish --target . --workspace ../private-routing-review \
  --purpose-reference REVIEW_REF --coverage CONSUMERS.json --execute-local
```

The runner uses the current Python/Node runtime, a minimal fixture environment
and explicit process/time/output bounds. Review the code before execution and
make its dependencies available in that environment without changing application
pins. It does not install packages or provide an OS sandbox. Provider/control
credentials are not inherited by checks.

Both frozen trees must pass. The command then refreshes current collector
confirmation/content authority again and prepares a checked local review ref in
the **private proposed repository**. The original source, index and refs remain
unchanged. The result returns both workspace paths; inspect `checks.json`,
`review.json`, `change.patch` and `rollback.patch` in the private handoff folder.
It creates no remote PR and never merges, changes traffic or deploys.

The ignored per-program journal binds the destination, exact source/proposal,
commands and review branch. Coverage freezes before checks. Interrupted copying
can use at most three fresh nonbillable attempts while preserving earlier files.
A dispatched check with no durable receipt stops for reconciliation; it is never
automatically rerun. A known complete receipt resumes without another check
execution. Failed checks cannot become a review. Current authority still applies
when reusing a receipt. This extension is under qualification and absent from
published PyPI dev5.

For separately managed workspaces, the lower-level commands remain available:

```sh
cutback routing program proposal --target . --purpose-reference REVIEW_REF \
  --output PRIVATE_CHANGE.json
```

The output must be outside the application tree. It refreshes current capture,
vendor, dataset, fit, selection and test-family authority and checks the unchanged
local source. A selected static model becomes one route over the original
declared input domain; a learned policy preserves that same domain. Instructions,
request framing and output bounds remain within the frozen source contract.

Prepare a separate clean proposed Git checkout, apply the exact diff and commit
it locally. Use [application checks](application-checks.md) to declare and execute
the affected consumer checks on **both** frozen trees. Then:

```sh
cutback routing program review CONTRACT.json RECEIPT.json PROPOSED_ROOT \
  --target . --coverage CONSUMERS.json --purpose-reference REVIEW_REF \
  --output PRIVATE_REVIEW.json --branch cutback/review-routing
```

This refreshes collector authority, verifies the exact two trees and receipts,
rejects unrelated changes and may create a local review ref. It does not create
a remote PR, merge, change traffic or deploy. The campaign PR publisher is not
implicitly reused for a different routing review contract.

## Accounting and safety

The shared PostgreSQL ledger reserves each physical native request atomically
against both child and parent budgets before dispatch. Parent ownership, current
approving principal, rights/vendor permissions, frozen hashes, prices and the
whole deadline are rechecked. A synthetic parent cannot authorize a static child
to construct live provider credentials. A missing/partial parent marker fails
closed. A run cannot simultaneously spend two different campaign/program budgets.

Cancellation and expiry stop new calls; they do not erase held reservations.
Late known bills update counters once without reviving cancelled work. Historical
retention keeps unresolved billing and referenced economic snapshots. Metadata
export and audited project deletion cover the new scoped records. Released
migrations remain unchanged; migration `0033` refuses a populated downgrade.

Native modeled spend is not full-task economics. Fitting/hosting/engineering
costs and production savings stay unknown. A passing sample never establishes
real customer quality, population coverage, production causality or payback.
Source files can change independently of a remote worker's frozen evaluation;
the application agent and final patch checks require the original source snapshot.
