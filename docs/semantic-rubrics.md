# Generate and review semantic rubrics for episode evaluations

This source-checkout workflow proposes rubrics from authorized training inputs and declared reference labels. Owners define up to three dimensions, their score thresholds, the overall episode pass-rate minimum and allowed regression, sample/group limits and a pinned judge model with input/output bounds. The generator cannot set those values. A separate review freezes the rubric text before validation and untouched-test confirmation. This extension remains under qualification in [progress](progress.md); the published PyPI dev5 release does not contain it.

## Try the independent sample

With the matching source installed and Docker Desktop running:

```sh
uv sync --locked --all-extras --dev
uv run --locked cutback demo --semantic-rubrics
```

No provider key is needed. Actual SDK, collector, PostgreSQL, encrypted artifacts and durable workers run through an owned synthetic provider transport. Thirty declared synthetic episodes alternate urgent and normal ticket references. The fixture gives the ordinary candidate the correct answer and deliberately makes the unsafe short-output fixture always answer normal. This demonstrates rejected changes and accounting; it measures no real model quality or customer benefit.

Open the printed local dashboard and choose **Explore sample → Inspect generated semantic rubrics**. In **Experiments → Reviewed evaluation criteria**, inspect the completed semantic version. Purpose-read its private content, compare the owner requirement, reference labels and proposed rubric, and inspect its separate generation budget. Its validation comparison includes the rejected unsafe fixture. The selected candidate's confirmation uses untouched groups. **Inspect the untouched proxy comparison** opens that result directly. Stop the launcher with Ctrl+C to clean up its own temporary database and ciphertext.

## Prepare your own permitted episode comparison

1. Import a frozen episode dataset under an explicit encrypted capture policy, with reference labels for the generator's training view. Keep known test families out of training. This extension supports the existing native fixture episode contract; it does not run arbitrary application tools. A plain text dataset is refused before generation.
2. In **Propose checks from training examples**, select **Episode semantic rubrics**. Declare each evidence source: reference correctness, actually returned retrieval evidence or observed conversation requirements. Enter the owner's requirement and per-dimension minimum. Declare the pinned judge's token limits and overall episode pass-rate/regression/sample/group limits. Freezing the one-call generation plan makes no inference request.
3. Inspect and authorize the exact generation hash and ceiling, then queue once. An enabled customer-owned worker uses its separate provider credential. Interrupted or uncertain billable operations retain their reservation and are never replayed automatically.
4. An admin purpose-reads the frozen requirements, authorized training view and generated rubrics. Approve or reject that exact version. Owner review is recorded separately from independent judge calibration. Renewal creates a new approval epoch and invalidates old bound plans.
5. Freeze a validation comparison. Its plan includes each target's native turn cap and one separately reserved judge call per episode/model/repetition. It forecasts all native and judge costs before a separate exact budget approval. Each native call rechecks current criteria, policy and principal authority. A model proxy may quote only the permitted reference, returned retrieval or observed conversation sources; invalid/missing judgments leave the episode score unknown.
6. Select a cheaper candidate that passes the declared proxy/episode gates. Freeze its separately approved untouched-test confirmation. The same criteria, judge, source rules and thresholds remain pinned. Inspect failed checks, invalid judgments, observed coverage, known costs and generation overhead. Overall episode pass rates apply the owner's declared tolerance; unavailable confidence intervals stay unknown.

Draft, validation and confirmation preparation use opaque request identities. The dashboard keeps these and validation nonces only in page memory, so a lost preparation reply can reconcile the same intent. A changed form creates a new intent. Reloading discards this memory: inspect retained server plans before preparing another one. The application agent's persistent native-text branch has its [separate durable recovery contract](agent-reviewed-criteria.md).

## Contracts and evidence limits

The generator uses the distinct `criteria-generation-v2` approval contract. Proposals must cover the exact opaque owner dimension IDs and training anchors; thresholds, test labels, executable evaluators and extra fields are rejected. Input, output, owner requirements and rubrics stay in the authorized encrypted plane. Public metadata contains opaque identity, frozen hashes, accounting and the criterion kind. Metadata does not reconstruct expired content.

Targets use `deterministic-v14`, with the existing native fixture runner and evidence-constrained semantic judge. Generation retains a shared one-time cost; each target comparison separately accounts for native and judge requests. Missing usage, outage, revocation, cancellation or uncertain receipts retain the existing conservative ledger behavior. No new schema is required: criteria versions/use bindings and their compound tenant constraints remain under immutable migration `0027` and its populated-downgrade refusal.

Results are labeled **model proxy comparison only**, including after untouched confirmation. Quotations constrain the judge's sources; they do not prove its reasoning or calibration. Scores are not independently verified semantic truth. Confidence, complete production task cost, amortized setup cost and realized savings are not manufactured. This evaluator cannot authorize the automatic native source patch: that path keeps its independently qualified source/evidence contracts.

The scoped `criteria` CLI and API also accept this requirement kind. Use `kind: "episode_semantic_rubrics_v1"`, owner `dimensions` and `judge` in the draft request. Validation adds an unjudged `episode_execution` contract; the server inserts the exact reviewed rubrics and pinned judge. It rejects attempts to change either. A caller can set a UUID4 `request_id` on confirmation preparation to reconcile the same nonbillable plan.

Acceptance exercises real PostgreSQL and installed native SDK transports, review withdrawal during a target call, rejected threshold mutation, invalid citations, preserved unknown scores, separate pre-call judge reservations, fresh confirmation, encrypted privacy boundaries and authenticated admin/viewer desktop/mobile accessibility. [Tests](../tests/integration/test_generated_semantic_rubric_workflow.py), [independent sample](../examples/semantic-rubrics/README.md).
