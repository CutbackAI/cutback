# Learned input-size routing

Source implementation under acceptance testing. Use the matching collector,
dashboard and client; this is absent from the published PyPI dev5 wheel.

Start with the [independent ticket application](../examples/learned-routing/README.md)
and `cutback demo --learned-routing`. Training, fitting, validation and untouched
confirmation are distinct operations. Preparing or fitting cannot authorize
vendor spend. Every inference stage requires its own exact plan approval.

## What is learned

The bounded fitter chooses increasing UTF-8 byte boundaries and one native
OpenAI or Anthropic target per leaf, minimizing known Decimal native cost on
complete paired training observations. Equal-size inputs share a decision;
duplicate rows cannot manufacture independent groups. Every selected observation
must satisfy the original owner metric and required deterministic checks. A
baseline tail covers longer inputs only within its approved bound. Inputs beyond
that bound refuse rather than dispatch a hidden fallback.

At most 256 training cases, five models, eight leaves, a 16 MiB evidence-read
budget and a bounded fitting deadline apply. Unknown costs cannot win. No feasible
lower-cost policy, missing coverage or an unknown baseline cost causes abstention.
Fitting makes no provider request. Size does not establish semantic difficulty;
training fit does not establish held-out quality or an advantage on a real task.

## Evidence and privacy

Training requests use the canonical pre-call reservation ledger with optimization
purpose. The server authenticates the frozen dataset, training split, independent
family assignments, current approval, exact retained case and completed native
call receipt before fitting. Missing, redacted, expired, uncertain, duplicated or
conflicting evidence refuses. Known test families cannot be relabeled as training.
Partial native output keeps unknown billing when the pinned adapter cannot price
it; its reservation is retained and the run cannot be automatically retried.

Cases, outputs and fitted policy remain in the authorized encrypted content store
under the original policy's rights, provider permissions, shared quotas and expiry.
Fit metadata exposes identifiers, hashes and counts. Private reads require an
administrator and audited purpose. Exports remain metadata-only; project deletion
removes retained fit ciphertext. Fitted policies are rechecked before each target
call; revoked training authority cannot become a successful comparison.

Policy and training-plan lists use bounded pages (default 50, maximum 100) with
explicit `offset` and `limit`; the dashboard can load more. Its comparison section
filters the latest 50 project runs loaded by the surrounding dashboard. A missing
older row there does not establish that no retained comparison exists.

Validation freezes the exact fitted policy and original quality requirements.
Confirmation selects one passing validation result and uses untouched groups
under a separate budget. Fixture transport is frozen into the plan; an ordinary
live worker refuses fixture-only execution before constructing a native request.

## CLI and interrupted operations

`cutback routing --help` provides `training-draft`, `fit`, `list`, `content`,
`validation-draft`, `confirmation-draft`, `approve`, `queue`, `execution`, `recheck`
and `result`. JSON draft bodies use stable UUID4 request identities; training and
validation also use a content nonce. Preserve the request file across a lost
reply. Reusing an identity with changed intent refuses. `approve` binds the exact
plan hash; `queue` consumes that approval once. `execution PLAN_UUID` reconciles
an interrupted queue reply. `recheck RUN_UUID` is limited to unstarted blocked
work and preserves its identity; it cannot replay an uncertain paid operation.

The dashboard makes at most one recovery attempt for a nonbillable preparation
after a transport timeout or storage-unavailable response. It preserves the exact
request identity and body. Approval, inference queueing and started work do not
use that retry. Late execution/private-read replies cannot replace a newly
selected plan or repopulate a cleared private review.

Each command takes `--project`, `--endpoint` and `--output`. Fit content also
requires `--purpose-reference` and an explicit private output path. Keep generated
content in the application's ignored workspace, outside publication inputs.

## Economics and application integration

The fitted training counterfactual, actual all-model training setup, validation
and confirmation spend are different quantities. Reports include a shared setup
key so the same training is counted once. Fitting compute, router hosting and
amortized full task cost stay unknown until separately measured. Synthetic usage
does not prove model quality, customer savings or payback.

The current workflow deliberately reports source-change eligibility as false:
the existing literal model editor cannot safely install a routing runtime. The
example shows an explicit native-client consumer and its isolated checks; a real
consumer requires its own current source binding, reviewed diff and affected
application checks. No production deployment, paid evaluation or automatic
source integration is implied by a passing synthetic confirmation.
