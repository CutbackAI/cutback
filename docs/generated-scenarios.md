# Reviewed development scenarios

This bounded source increment is accepted in [PR 57](https://github.com/CutbackAI/cutback/pull/57), with exact artifact/hosted evidence in [progress](progress.md). It is absent from published PyPI dev5. It adds one approved native scenario-generation call, synthetic personas, owner-declared coverage goals, private review, and import into a development-only validation dataset. The independent sample uses mock HTTP transport through the actual supported SDK; it makes no paid provider request.

Run the independent local example from the repository checkout with Docker available:

```shell
python -m pip install -e ".[server,openai]"
cutback demo . --generated-scenarios
```

The demo creates a separate synthetic support workflow, freezes authorized training examples, approves one synthetic generation call, records a demonstration review, imports the reviewed variants once, and separately approves a model comparison on those development cases. The dashboard is available at the local address printed by the command. Credentials stay in the local demo workspace and browser memory. Demo approvals are synthetic fixtures; they do not establish real human review or customer results.

For another proposal in the demo dashboard, open **Experiments → Generated development scenarios → Expand training scenarios** and select **Owned synthetic demonstration only** as the execution mode. The sample worker accepts only this sample project's fixture-generation plans and linked development evaluations. Configured-provider plans require a separately configured real worker and explicit cost approval.

## What generation can change

An owner supplies a task brief and one to eight coverage goals. Up to eight permitted training cases provide opaque anchors. The generator can return up to four synthetic persona descriptions and eight scenario variants, with additional user context and up to three follow-up turns per variant. The request freezes these limits and the one-call budget. Coverage counts describe the generator's declared mapping; omitted goals remain visible with zero proposed cases.

The materializer retains each original question, reference, gold, system instructions, tool definitions, permissions, final-state assertions, and turn limits. A plain text task cannot acquire tool or multi-turn execution. Additional follow-ups fit inside the original synthetic conversation limit. Unsupported isolated-record and paired security contracts are refused explicitly. The generator does not receive gold labels, reference answers or tool results. Only authorized training questions and existing user follow-ups enter its request.

These are static development variations. The personas do not provide a reactive user simulator. Copied source labels and assertions may be inappropriate for a particular added context: an owner must inspect and reject such proposals. Generation does not prove semantic equivalence, population coverage, independent labels, realistic users, or independent confirmation quality.

## Agent workflow

Create a private JSON request file from the API's `ScenarioDraftInput` schema. It contains `dataset_id`, `requirements`, `generator`, `budget`, optional `max_training_examples`, and optional transport. Use the existing OpenAI Responses models qualified for this operation. The collector needs current encrypted-content authorization for the dataset's environment/workflow and the OpenAI destination. Production workers require an explicitly configured provider credential and execution enablement; supplying a generation budget is a separate decision.

The resumable workflow generates and saves a stable request identity before contacting the collector. Use a private output directory outside publication inputs:

```shell
cutback scenarios prepare-workflow private/request.json --target . --project PROJECT_UUID --endpoint http://127.0.0.1:8080 --output private/prepared.json
cutback scenarios continue-workflow --target . --project PROJECT_UUID --endpoint http://127.0.0.1:8080 --plan-hash EXACT_PLAN_SHA256 --approval-ref OWNER_BUDGET_REFERENCE --output private/current.json
cutback scenarios continue-workflow --target . --project PROJECT_UUID --endpoint http://127.0.0.1:8080 --output private/current.json
cutback scenarios read-workflow OWNER_READ_PURPOSE --target . --project PROJECT_UUID --endpoint http://127.0.0.1:8080 --output private/proposal.json
cutback scenarios review-workflow EXACT_VERSION_SHA256 approve OWNER_REVIEW_REFERENCE --target . --project PROJECT_UUID --endpoint http://127.0.0.1:8080 --output private/imported.json
```

Replace uppercase placeholders with the actual scoped values. Configure the control credential through `CUTBACK_CONTROL_TOKEN`, never a committed file or command-line argument. Preparation does not dispatch inference. The first continuation requires explicit approval of the exact generation plan. Later continuations poll/reconcile the same parent/run. If a queue reply is uncertain, continuation issues only status reads; it does not create another run. An owner reviews the private proposal before the last command records that explicit decision and imports approved development data. The workflow does not make a review decision itself.

The private `.cutback/scenario-agent/session.json` stores scope, immutable identities, current state, and metadata receipts. It does not copy the task brief, original cases, persona text or generated output. A changed request, collector or project is refused. Lost preparation and import replies reuse the saved identity. A failed or uncertain billable generation stops; no automatic retry grants another call. Target evaluation requires a separate experiment plan and budget after import.

Individual controls are also available through `cutback scenarios draft`, `show`, `approve-generation`, `queue`, `content`, `review`, `import`, and `cancel`. Direct preparation/import JSON requests require stable UUID4 `request_id` values. Content reads require admin scope, a purpose reference and a protected output file. Read and review actions are audited.

## Data and authority

Source snapshots, native output and imported cases use the explicitly authorized encrypted store. Public status/export contains identifiers, counts and accounting metadata. Input/output reservations share the capture-policy quota with all other content. Each physical native call first reserves through the canonical project/environment ledger. Interrupted, invalid, unknown-charge and cancelled calls retain their accounting status rather than becoming successful proposals.

Imported originals and their variations share canonical source families, including across dataset imports. All imported cases are assigned to validation: train and test counts are zero. Their immutable dataset hash binds the actual assignment. Ordinary dataset import cannot mint an internally reviewed generation link. The generation version and exact review approval epoch remain required at plan creation, queue, dataset load, every target-call reservation, and source-evidence checks. Rejecting/replacing the review, losing reviewer role, deleting/expiring authorized content, or revoking source policy invalidates use of existing derived datasets. Reapproving a new epoch does not restore an old imported epoch.

Project metadata export includes generation/import records without plaintext content. Project deletion removes encrypted objects before relational records. Derived payload expiry is capped by the original generation's source deadline. Released migrations remain immutable; the additive scenario migration is unreleased until its complete acceptance gates pass.

## Acceptance

`tests/unit/test_scenario_generation.py` exercises the closed proposal schema, bounded native frame, source-preserving materialization and family grouping. Real PostgreSQL integration tests cover migrations, tenant foreign keys, approval/review epochs, shared quotas, idempotency and uncertain calls. The agent integration test drops preparation, queue and import replies and checks canonical identities. `tests/end_to_end/test_generated_scenarios_pipeline.py` exercises HTTP → native SDK → ledger → encrypted PostgreSQL-backed generation → review → import → development evaluation, plus authenticated authoring, viewer denial, mobile and axe accessibility. Exact installed wheel, SDK 3, image recovery, publication-boundary and hosted checks are separate gates recorded in [progress](progress.md); source tests do not imply they have passed.
