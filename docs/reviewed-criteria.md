# Generate checks, review them, then compare models

This source-checkout workflow proposes bounded deterministic evaluation criteria from up to eight authorized training examples. It uses the existing encrypted store, PostgreSQL authority, native OpenAI Responses executor and durable pre-call ledger. Generation is a separate one-call operation; it is never reported as a target-quality evaluation.

Start with the [independent example](../examples/reviewed-criteria/README.md). To explore the whole zero-paid-call local sample, run `uv run cutback demo --reviewed-criteria`. Its generator and target calls pass through installed native SDKs and an owned synthetic HTTP transport. Its approvals are explicitly synthetic demonstration approvals, not a human judgment or evidence of customer savings. The newer commands are not in the published dev5 wheel.

## Dashboard journey

1. Install matching source, migrate a disposable PostgreSQL database, and configure the authorized encrypted store. Connect to the collector with a scoped project key; provider credentials remain on the worker.
2. In **Experiments**, import independently labeled examples under a current capture policy. The generator needs training rows; known test families cannot be relabeled as training through a different import.
3. Open **Propose checks from training examples**. Select the dataset, describe the task and set the minimum score, allowed regression, minimum confirmation samples/groups and one-call generation budget. Freezing a draft makes no provider call.
4. Inspect the exact plan hash. An admin approves its generation budget; an analyst queues it once. The customer-owned worker must separately enable experiment execution. Refresh status after an interrupted response; repeated queue submissions return the same run and never replay a failed or uncertain billable request.
5. An admin supplies a read purpose to inspect the frozen requirements, training rows, proposed checks and exact deterministic configurations. Reject unsuitable proposals, or approve the exact content version. This approval does not approve target-call budgets.
6. Freeze a validation comparison against the exact current criteria approval. Approve and queue its separate budget in **Experiment plans**. The thresholds and all required checks remain unchanged.
7. Select one passing, cheaper validation candidate and freeze its untouched-test confirmation. Approve its separate budget and run it once. An uncertain or already consumed holdout cannot be reused as fresh evidence.
8. Review sample results and independent-group sufficiency. Source changes retain the existing exact request/source and rollback requirements. A reviewed criterion version cannot grant a new source adapter or a production deployment.

Analysts can prepare and queue permitted operations; admins control generation budgets and private criterion review; viewers see bounded metadata. Private content stays in page memory and can be cleared. Changing projects discards it. Browser storage does not retain credentials or content.

## Contract and evidence

The generated response may contain only a bounded list of descriptions, approved metric kinds, optional bounded output fields/schema and opaque anchors into the training view. Supported proposals use `exact_label`, `field_f1`, `numeric_match`, `json_schema` or `nonempty`. Generated thresholds, labels, executable evaluators, external references, regex schemas, budgets and test cases are refused. All thresholds come from the owner's original requirements. Descriptions, training content, schema details and output-field names stay in encrypted artifacts; public plan metadata redacts content fields.

`criteria-generation-v1` binds the source dataset, encrypted input hash, generator request/price contract and one-call budget. The retained proposal is immutable. Review decisions append audit events and create an opaque approval epoch. Rejecting, renewing or revoking that review invalidates old plans; reapproval does not reactivate old plan bindings. Each native target call rechecks current criteria approval, data rights, expiry and principal scope. Target comparisons use the separate `deterministic-v13` contract; existing evaluator contracts keep their serialized bytes.

Generation reserves its output slot under the shared capture quota before dispatch. Known native charges survive cancellation, revocation and output-storage failure. Missing usage remains unknown; a crashed operation is not automatically replayed. Content expiry can leave metadata pointers, but missing content cannot reconstruct criteria or authorize a later evaluation. Export includes metadata and opaque use bindings; project deletion removes encrypted objects before relational references. Migration `0027` refuses a populated downgrade.

Reports keep the shared one-time generation cost separate from target evaluation spend. They do not charge it again on every use or invent an amortization denominator. Full production task cost, realized savings and population coverage remain unknown. Owner review is not independent judge calibration. A schema/nonempty rule cannot establish semantic correctness, and a generated rule cannot create independent ground truth.

## CLI and API

`cutback criteria --help` exposes `draft`, `show`, `approve-generation`, `queue`, `content`, `review`, `evaluate`, `confirm`, `source-evidence` and `cancel`. All use an explicitly selected project/endpoint and the existing `CUTBACK_CONTROL_TOKEN` environment variable. Private content commands require `--output` into a protected local file; keep that file outside public repositories. `source-evidence` revalidates the current criteria and untouched-test lineage before returning the private native request needed to prepare an exact model diff; it grants no new source adapter or deployment permission.

The corresponding project-scoped routes are `/criteria/drafts`, `/criteria/{id}`, `/criteria/{id}/approve-generation`, `/runs`, `/content`, `/review`, `/evaluation-draft`, `/confirmation` and `/cancel`. `evaluation-draft` takes the exact `criteria_binding`, explicit baseline/candidates and a separate budget. Confirmation derives its single candidate from completed validation; arbitrary changes to criteria, native requests or the selected candidate are refused. Metadata GETs and draft preparation make no provider request.

The separate [semantic episode rubric extension](semantic-rubrics.md), under source qualification, adds reviewed rubrics and evidence-constrained native model proxies. Independent calibration, simulation personas, generated multi-turn scenarios and selection across cache, routing, retrieval and training remain separate unfinished work.
