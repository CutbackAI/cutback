# Imports, blind review and dataset lineage

Status: unreleased source checkout. These endpoints and the **Review** dashboard are separate from published PyPI dev5. No importer or review operation runs inference, user code, external judges or a production deployment.

## Qualified source projections

`cutback datasets preview-import FILE --profile PROFILE` runs offline and prints a content-free mapping, counts, rejection codes and source SHA-256. It never uploads examples. A file must be ordinary UTF-8 JSON/JSONL, at most 1 MiB, with no duplicate JSON fields, nonfinite numbers or nesting beyond 20. Symlinks and archives are unsupported. No filesystem include, URL fetch, evaluation script or provider config executes.

| Profile | Accepted projection | Evidence limits |
|---|---|---|
| `cutback-jsonl.v1` | Native strict `Case` rows; input, optional declared gold/reference/group/stratum | Gold is a source-declared expectation until independently reviewed |
| `openinference-otlp-json.v1` | OTLP JSON, at most 200 spans; `input.value`/`output.value` string attributes; hashed session group | Historical output is a reference. No inferred gold, native replay support, spend or universal Phoenix compatibility |
| `promptfoo-0.125.1-results.v3` | Actual version-3 results envelope; `testCase.vars.input`, string `response.output`, one optional literal `equals` expectation | Source success/score/failure kind are externally asserted; scripts, prompts/config, reasons, grader execution and costs are discarded |
| `bedrock-automated-model-jsonl.v1` | Documented automated text-model JSONL; explicit prompt, optional unreviewed reference and one historical model response | Raw numeric/boolean/null metrics; model identifier and arbitrary metric names stay in encrypted content. Human/RAG formats require another adapter |
| `azure-ai-evaluation-1.18.10-results.v1` | Actual pinned classical SDK `rows` export; explicit query/response/ground truth and optional text context | Scalar source metrics, separate boolean/missing/error/skipped states, no inferred scale. Native grader output-item envelopes require another adapter |
| `vertexai-1.153.1-metrics-jsonl.v1` | Actual pinned `EvalResult` pointwise table exported as records JSONL; prompt/response/reference and optional context | Original `metric/score` values, including null; pairwise/conversation/multimodal exports are outside this profile |

The [cloud export walkthrough](../examples/cloud-evaluation-imports/README.md)
documents exact producers, limits, independent fixtures and the optional locked
producer environment. It does not configure a cloud account or invoke a model.
Arbitrary metric names and source model identifiers stay in authorized encrypted
preview content, outside dataset provenance metadata. Raw assertions remain
external; neither their scores nor a supplied reference establishes independent
labels. No normalization across provider-specific scales is inferred. An explicit
source error or skip suppresses a stale usable score; null remains missing.
Ordinary cloud dataset metadata contains at most 25 safe assertion examples,
with the complete projected assertion count and explicit truncation/scope flags.
The authorized full projection remains encrypted in the import preview. These
examples cannot reconstruct unretained quantiles, task denominators or population
coverage. Existing legacy assertion metadata is unchanged.

Adapters report row-level fixed rejection codes without echoing source field names or examples. Preview metadata includes the first 25 row fingerprints and first 100 rejection diagnostics; totals remain explicit. Structured numbers that cannot survive conversion exactly are rejected rather than silently changed. The [actual pinned producer example](../examples/review-workflow/README.md) documents regeneration and explicit compatibility limits.

`POST /v1/projects/{project}/imports/preview` requires analyst rights, an active explicit content policy, environment/workflow, source text and its exact UTF-8 SHA-256. The encoded JSON request must also fit the collector's 1 MiB body limit. It stores only the projected cases/source assertions in the encrypted plane and creates no dataset. Unknown config/attributes are not retained. The browser requires upload authorization and shows the frozen mapping before commit. The creator or a project administrator may purpose-read at most 25 projected examples/assertions; displayed examples record development exposure and cannot race a protected confirmation. Metadata resume/list endpoints never return content. `POST …/imports/{preview}/commit` requires the exact preview hash, explicit mapping/rights confirmation, and explicit acceptance if any rows were rejected. It atomically creates one new **unreviewed** dataset; concurrent duplicate commits return the same dataset. Current scope, expiry and shared policy quotas apply on every step. Failed SQL/storage writes compensate newly created encrypted objects.

## Frozen blind review

Project administrators create queues with `POST /projects/{project}/reviews` from up to 200 authorized dataset items or captured trace-content links. Metadata-only spans cannot invent missing inputs. Queue configuration freezes sources, rubric, reviewer credential identities, a separate adjudicator, required count (2–5), labeling/calibration mode and provenance. Configuration, source copies, votes, rationale and resolutions are separately encrypted. Relational records contain safe IDs, hashes, roles/status/times; low-cardinality judgment content hashes include a random nonce to resist guessing labels from metadata exports.

Assignees require current project analyst/editor/project-admin grants. OIDC subjects use the existing hashed actor identity; scoped keys use their UUID, never their token. Every claim/read/submission/adjudication rechecks assignment and current authorization. Administrator status alone does not bypass the reviewer/adjudicator assignment. Source and review policies must remain enabled, unexpired and in scope; source deletion/expiry/revocation stops access even if an encrypted review copy still exists. Distinct credentials are not proof of separate people. Promotion's human independence assertion is an explicit owner attestation, preserved as such; fixtures stay synthetic.

The administrator's bounded principal directory lists eligible scoped credentials and OIDC members without tokens or key digests. The dashboard offers those identities as reviewer selections and a separate adjudicator. Labels start blank. Metadata queue/item status remains readable after content expiry; administrators can explicitly close a queue and release leases without decrypting any content. Closure preserves submitted votes and prevents new submissions or promotion. Queue creation creates a new frozen version; after an uncertain nonbillable creation reply, inspect the queue list rather than blindly creating repeated copies.

State advances `ready → leased → reviewed → resolved`, or `reviewed → adjudication → resolved` when the required categorical judgments disagree. A reviewer has at most one live lease per queue. Multiple reviewers can claim the same item up to the frozen quorum. Leases last 15 minutes by default, configurable 30 seconds–one hour. Release, expiry and revocation free capacity without deleting submitted judgments. A stale lease cannot submit. Same-vote retries are idempotent; changed votes conflict. To revise a rubric or completed judgment, create a new queue/version; historical answers remain immutable.

Purpose-read reviewer endpoints return input, historical reference, frozen rubric and the requesting reviewer's own submission. Source gold and peer answers are withheld by server authorization. Only the assigned adjudicator may read anonymous completed votes after quorum/disagreement; the adjudicator cannot also be a reviewer. Categorical consensus requires exact decision/label equality; there is no numeric averaging into fabricated gold. Adjudication adds an immutable resolution, preserving all votes.

`POST …/reviews/{queue}/promote` requires administrator rights, exact queue hash, current rights confirmation and all items resolved. It excludes explicitly rejected items, refuses an empty result, and creates one idempotent **new frozen dataset** with source item/trace IDs, queue hash, vote IDs and resolution receipts. It never edits the source dataset, existing plans or run evidence. Representative coverage remains false until independently established. A calibration queue hides known gold, reports exact-match counts to administrators only after completion, and cannot be promoted. Small fixture counts do not establish reviewer generalization or real customer quality.

The [step-by-step synthetic walkthrough](../examples/review-workflow/README.md) covers the dashboard, separate reviewer sessions, disagreement and promotion. API schemas are generated in [OpenAPI](../web/openapi.json).

## Persistent group identity and untouched confirmation

Each project retains canonical dataset families. Aliases join NFKC/case-folded/whitespace-normalized inputs with digit runs collapsed, plus explicitly declared customer groups. Reimports with new case IDs, dataset names, provenance or split seeds do not create fresh identities. New group connections merge canonical families transitively. Original family IDs, encrypted case bodies, group hashes and split assignments remain unchanged; old datasets whose formerly separate groups now overlap must be re-frozen. Semantic paraphrases are not universally detected: this mechanism cannot prove all real-world examples are independent.

Dataset hashes bind original content, split/group/family assignments and extension provenance. Exposure receipts are written **before** queued evaluation/training can call a provider, under a project-scoped PostgreSQL transaction lock. Unknown legacy lineage cannot authorize a fresh test claim. Validation, training and selected review examples consume development exposure. Concurrent protected confirmation cannot race another evaluation/review of those groups. A crashed or canceled run conservatively keeps its receipt: an uncertain operation is not automatically safe to repeat.

Ordinary monitoring may repeat test metrics, but repeated/unknown test groups cannot earn a new independent quality recommendation or source patch. Results preserve the descriptive sample gate and explain `NEEDS_NEW_CONFIRMATION`. Staging, patch upload and rollout proposal also recheck current canonical families: a later discovery of overlap invalidates eligibility without rewriting historical measurements. No alias change or reimport resurrects a consumed holdout.

For pre-0008 datasets, `POST /projects/{project}/datasets/{dataset}/lineage/reconcile` requires administrator rights, exact existing immutable hash, declared purpose and still-authorized encrypted content. It adds supplemental family identities while preserving old content/hash/splits and conservatively marks all recovered groups **exposed**, without claiming they were untouched. This operation cannot recover deleted/expired content. An unrecoverable legacy test history keeps fresh confirmation unavailable in that project; do not manufacture identities or erase history to assert freshness.

## Retention, export and deletion

All content uses the existing authorized encrypted store and the shorter policy/retention expiry. Review configuration, source copies, votes and resolutions count toward the same lifetime policy sample quota as imports/cache/other payload objects. Reads are purpose-audited and browser credentials stay in memory. Metadata export includes workflow receipts but not encrypted content, tokens or object references. Expiry maintenance deletes encrypted bytes while retaining safe workflow/exposure receipts. Content access then fails visibly. Group hashes and exposure receipts remain until project deletion to prevent expiry from silently resetting a holdout. Exact project deletion removes review records in dependency order, deletes encrypted objects and cascades family aliases/exposures; backup expiry remains the operator's documented policy.

No deployment, live inference, customer outcome, independent human participation or provider compatibility beyond these qualified profiles is established by these engineering tests.
