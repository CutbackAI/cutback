# One budget for a source policy

This source feature is being qualified. PyPI `0.1.0.dev5` does not contain it.
For applications already declaring exact cache, inspect their actual retained
[policy activity](policy-runtime-activity.md) before choosing an improvement
reference. Configured cache is not proof it ran. Automatic cached-current
reference evaluation remains an explicit open gate.
It supports the supplied fixed, stateless sync or async OpenAI Responses consumer with a
plain text input and exact-label evaluation. See [current acceptance](progress.md)
before treating a source checkout, wheel or container as a tested release.

The program compares simple native alternatives first. If those fail, it may
generate bounded instruction/output-limit alternatives using training examples
and validation aggregates. If search fails, it can gather native training
receipts, fit input-size routing without another provider call, and validate the
result. It freezes one choice before the full untouched test split. It never
selects a different engine after looking at those test outcomes.

All reference calls, failed alternatives, generation, training, validation and
confirmation share one durable request/cost envelope and absolute deadline.
Known cache-operation upper bounds are included. Fit compute, application
hosting, invoice reconciliation and production savings remain unknown.

## Try the independent application

From a current checkout with Docker Desktop and Git available:

```sh
uv sync --locked --extra server --extra openai
uv run cutback demo --source-policy
```

Open the loopback dashboard printed by the command and choose **Explore sample**.
The one-budget walkthrough explains its four stages and displays the exact
program ID. Choose **Follow the one-budget improvement**, then inspect that program. Inspect the static
comparison, the conditional training/validation path and the single confirmation.
The sample deliberately makes the small model fail some long tickets. The
smaller output-cap alternatives also fail. Those fixture failures are intentional.

The sample creates a separate application, `policy.json`, eighty synthetic ticket
cases and an offline native consumer check. Its receipt points to the original
application and separate base/proposed Git trees. Both trees are checked. The
inverse diff must recover the exact original bytes before the forward diff is
restored. The application you started from is unchanged.

Every provider response and usage number is supplied by a closed synthetic
transport. No provider API key or paid request is needed. This proves the
mechanics, not real model quality or customer savings. Ctrl+C stops the owned demo
and removes its temporary database and encrypted artifacts.

To inspect the application without a collector:

```sh
uv run cutback source-policy sample ./independent-policy-example
uv run python ./independent-policy-example/check_application.py
```

Use the same source environment's Python for the last command. The application
borrows the native client; the check supplies its own offline HTTP transport.

For an asynchronous application, copy its qualified consumer and actual async
native check:

```sh
uv run cutback source-policy sample ./independent-async-policy-example --async
uv run python ./independent-async-policy-example/check_application.py
```

The async consumer awaits the caller's `AsyncOpenAI` response, preserves the
caller's retries and cancellation, and returns the native response object. Both
adapters retain the same bounded nonstreaming request frame. Async exact caching
is explicitly unsupported: the scanner, import, plan and runtime refuse that
combination rather than silently running it uncached. Streams, arbitrary async
wrappers and additional client surfaces remain separate adapter requirements.

## Use permitted data in an application

1. Commit the application and qualify its complete consumer. The supported
   pattern is in [the independent example](../examples/source-policy/README.md).
   Qualification parses the full producer and configuration; it never imports
   application code. Arbitrary wrappers, tools, stateful requests, custom
   endpoints and streaming consumers require another qualified
   adapter. An unsupported consumer is a blocker, not permission to replace its
   behavior.
2. Configure a collector with real PostgreSQL, current migrations, encrypted
   content storage, a scoped project administrator credential and explicit
   payload authorization. Prepare a dataset with current rights, independently
   checked labels and disjoint train/validation/test input families. Record a
   complete baseline task-cost snapshot. [Onboarding](application-onboarding.md)
   and [task economics](task-economics.md) describe those prerequisites. A metadata
   scan cannot invent historical costs, examples or task outcomes.
3. Inspect compatible metadata and freeze a preview. Substitute your endpoint, declared
   opportunity assumptions and maximum spend:

   ```sh
   cutback source-policy discover --target . --producer app.py \
     --endpoint https://YOUR-COLLECTOR --project PROJECT_UUID \
     --environment staging

   cutback source-policy prepare --target . --producer app.py \
     --endpoint https://YOUR-COLLECTOR --project PROJECT_UUID \
     --data-environment staging \
     --purpose-reference improvement-review --max-usd 2 \
     --max-requests 200 --max-wall-seconds 600 \
     --savings-per-success 0.001 --forecast-successful-tasks 100000
   ```

   When IDs are omitted, preparation selects only a unique compatible retained
   dataset and complete declared task-cost snapshot for this workflow/environment.
   `discover` lists IDs and hashes without reading encrypted examples or policies.
   It considers at most 100 dataset summaries and 100 snapshot summaries. A full
   dataset page or a remaining snapshot cursor leaves selection incomplete.
   Missing independent labels, test cases, costs or ambiguous versions require
   the corresponding input; nothing chooses a version by recency. To choose
   deliberately, pass `--dataset DATASET_UUID --baseline-snapshot SNAPSHOT_UUID`.
   Retried preparation retains the saved IDs even if another version appears.

   Metadata compatibility does not verify baseline source alignment, population
   coverage, label quality, current content rights or causal savings. Automatic
   preparation refreshes the chosen frozen report's hash and actual workflow,
   service and release identities. The collector still enforces content expiry,
   rights, lineage and the final program contract before execution can be approved.

   In PowerShell, put the same arguments on one line:

   ```powershell
   cutback source-policy prepare --target . --producer app.py --endpoint https://YOUR-COLLECTOR --project PROJECT_UUID --data-environment staging --purpose-reference improvement-review --max-usd 2 --max-requests 200 --max-wall-seconds 600 --savings-per-success 0.001 --forecast-successful-tasks 100000
   ```

   Supply `CUTBACK_CONTROL_TOKEN` privately in the process environment. The
   metadata journal contains IDs, hashes and intent, not that credential or raw
   instructions. Omit `--endpoint`/`--project` when a scoped saved connection
   already exists; use `--local` only for an explicitly configured local stack.
   `--no-generate` disables instruction search. The default generator has two
   rounds of at most two variants; its entire worst-case effort is priced into
   the preview. No provider request or approval occurs during preparation.
   Unknown economics or an unaffordable complete path causes abstention.
4. Inspect the exact private policies and requirements through the dashboard's
   purpose-scoped content action. Review the whole envelope, including failed
   trials. Explicitly approve the returned hash and your approval reference:

   ```sh
   cutback source-policy continue --program-hash EXACT_HASH \
     --approval-reference YOUR_AUTHORIZATION
   cutback source-policy run --max-wait-seconds 300
   cutback source-policy status
   ```

   The collector's configured worker executes approved native plans. A local
   operator can instead use `run --local-worker --max-operations 10` with the
   matching configured stack and documented provider credentials. This does
   not authorize spend independently of your explicit approval. Repeating `run`
   resumes the same identity; it does not increase the budget. Stop with
   `cutback source-policy cancel`. A lost/uncertain billable reply stops further
   requests and retains its reservation. Do not reset it to queued or retry it.
5. Prepare a source review outside the application:

   ```sh
   cutback source-policy finish --workspace ../private-policy-review \
     --purpose-reference source-review
   ```

   Inspect the previewed command keys and both private trees. Declare which
   existing commands cover each consumer in a private JSON object, for example
   `{"synthetic-source-policy":["native-policy-application"]}`. Use the actual
   detected or explicitly provided command keys, not this example blindly.
   Review commands before executing them: the runner is a bounded process runner,
   not an OS sandbox. Then run the same handoff with that coverage:

   ```sh
   cutback source-policy finish --workspace ../private-policy-review \
     --purpose-reference source-review --coverage ../private-coverage.json \
     --execute-local
   ```

   The result includes `checks.json`, `review.json`, the forward/inverse patches
   and an optional local review ref. The server refreshes confirmation/content
   authority after checks. A retained receipt avoids repeating known checks;
   dispatched checks without a receipt require explicit operator review. The
   workflow does not push, merge, deploy or alter the original application's refs.
6. Review evidence scope. Synthetic and sample-only results stay local review
   evidence. A held-out gate also requires current independent test lineage,
   paired receipts and the declared statistical/quality requirements. Verify
   every additional consumer yourself; coverage is owner-declared. After a
   terminal program has complete known accounting, `source-policy archive`
   preserves its metadata journal and permits preparation for a new revision.
   It cannot transfer the prior approval or discard an unresolved bill.

## Publish a qualifying checked policy as a draft

This follow-up is under qualification; check the current acceptance record before
using it. Complete `source-policy finish` with the reviewed commands, coverage and
explicit local execution first. The declared coverage must include the producer's
known workflow; it does not establish that all external consumers were discovered.
For a current held-out review, preview the exact repository, branches, checked
commit, changed file and publication intent:

```sh
cutback source-policy publish --target . --repository OWNER/REPOSITORY \
  --base-branch main --head-branch cutback/source-policy-review \
  --purpose-reference policy-publication-review
```

The preview refreshes collector confirmation/content rights and re-verifies the
source proposal, both checked trees and retained application receipt. It makes no
GitHub request. The changed policy file contains application-owned instruction
text. Review that file and the private diff before explicitly uploading it. Set
`CUTBACK_GITHUB_TOKEN` privately in the process environment, then repeat the exact
command with `--publish`. No ambient provider or Git CLI credential is used.

Publication uses the existing bounded draft publisher: one checked source blob,
tree and commit, against the exact remote base. It sends no local ancestors, check
output, evaluation examples or credentials. An existing different branch, moved
head/base, closed or mismatched PR stops reconciliation. Each mutation is
journaled before dispatch; uncertain unobservable writes are not blindly retried.
The metadata journal is `.cutback/agent/policy-program-publication.json`.

Synthetic/sample-only policy results remain local reviews and are refused before
GitHub access. Cached policies still require separately qualified runtime checks
and cannot use this publication path yet. The command creates a draft for human
review; it grants no merge, deployment, traffic change or production-savings
authority. A retained review cannot bypass later revoked content rights.

If upgrading a handoff created before publication inputs were recorded, repeat
the same `finish --execute-local` command and workspace with its original
coverage. Known retained checks are reverified rather than rerun; a dispatched
check with no receipt still requires operator review.

## Optional exact cache

Pass an explicitly authorized `--exact-cache-contract PRIVATE_CONTRACT.json` at
preparation to include an exact-cache control under the same budget. A known
operation upper cost and measured repetitions in the frozen validation inputs
are required. The compiler may omit it before approval; it never substitutes an
invented production hit rate. The full cache contract and encrypted native
response runtime are described in [exact caching](native-response-cache.md).

A policy diff contains no cache token, HMAC key or capture-policy credential.
The application activates cache only when its owner explicitly sets the private
`CUTBACK_NATIVE_CACHE_SCOPE_FILE`, bound to that whole policy revision and cache
contract. Ambient configuration cannot enable cache for an uncached policy.
Changing `policy.json` alone does not activate runtime caching. Until runtime
configuration and its application checks are separately qualified, a cached
choice reports `CACHE_RUNTIME_CONFIGURATION_REQUIRED`, not a ready PR.

When a cache contract is supplied, preparation automatically looks for retained
source-matched workload analyses in `--workload-environment` (default `staging`).
For ordinary provider transport, an absent, changed, unprofitable or unqualified
workload omits the cache control and prepares the other native alternatives.
The CLI explains that decision; it does not fill missing evidence with zero.
An explicitly synthetic fixture can still compare cache on its frozen validation
inputs, with that narrower basis recorded. The actual included controls determine
the frozen budget and program hash. Observed workload metadata cannot approve
calls, substitute for independent confirmation, enable runtime caching or become
a production hit-rate claim. A new preview after changed eligibility may require
review of the existing frozen intent rather than silently changing it.

## Observe the application before considering cache

The copied sample also contains `observe_application.py`, a separate process
entry point that makes seven real SDK calls through a closed synthetic HTTP
transport. Without instrumentation it prints `observer_health.enabled: false`.
It does not contact a provider or establish real quality or production savings.
Run it with the same environment used for the consumer check:

```sh
uv run python ./independent-policy-example/observe_application.py
```

Keep the fixed `app.py` unchanged. Preview the external startup change, inspect
the diff, then apply that reviewed change and refresh attribution after commit:

```sh
cutback instrument --target ./independent-policy-example \
  --entrypoint observe_application.py --plan
cutback instrument --target ./independent-policy-example \
  --entrypoint observe_application.py --apply
# Commit the reviewed source changes in that application's Git repository.
cutback source-map --target ./independent-policy-example
```

Applying observation inside the fixed consumer invalidates its full-program
qualification. The
scanner recognizes the native wrapper without reading `policy.json` or importing
the application. Its static model and costs remain unknown. The reviewed startup
requires explicit `CUTBACK_ENABLED=true`, service/environment, collector endpoint,
project ID and scoped ingest credential in the process environment. Default
observation captures metadata only. Commit the reviewed bootstrap and regenerate
the source map so runtime events report the current client-reported commit.

For an uncached synchronous native reference, explicitly authorized encrypted
native request-profile capture can support a separate workload assessment. It
needs a current capture policy, its private HMAC key and the documented profile
analysis worker. See [request profiles and reuse](request-reuse.md). Then:

```sh
cutback source-policy assess-workload --target . --producer app.py \
  --endpoint https://YOUR-COLLECTOR --project PROJECT_UUID \
  --exact-cache-contract ../PRIVATE_CACHE_CONTRACT.json
```

This reads at most one hundred analysis summaries and assesses at most five
eligible windows against the exact current source, commit, callsite, model,
freshness and TTL. The server rechecks current rights, retained metadata census,
coverage and complete known modeled provider costs without decrypting payloads.
Changed metadata, expiry, revoked rights and unknown coverage stop qualification.
The result in `.cutback/agent/policy-workload.json` is metadata only. It can
recommend a separately approved trial; it cannot approve inference, activate
cache, alter a policy, claim production hit rate or supply independent labels.
Source lineage is client-reported attribution, not independent attestation.

Async observation works for metadata and authorized payload capture, but native
async cache fingerprints remain unqualified. Current input-size routers also
require separate multi-model workload qualification. `assess-workload` refuses
both before sending HTTP. Ordinary native literal-source assessment retains its
separate binding and API; policy attribution cannot become a literal edit span.

## Current boundaries

The initial/current reference must be uncached. A previously changed native
instruction/model or source-bound input-size router can be the next reference.
The comparison executes that current router's actual native leaf for every
input. It does not substitute the policy's historical native anchor. Its plain
native controls receive their own calls and costs. Conditional refitting uses
only authenticated paired training receipts and preserves the current router's
native targets above the largest training input. That preserved tail has no new
quality claim. The result still needs independent validation and one untouched
confirmation before a checked source review.

For another revision, retain the earlier review artifacts, explicitly apply and
commit the reviewed source yourself, then use a new independently labeled
dataset with unexposed test families and a current complete cost snapshot. After
the earlier program is terminal and all bills are known, `source-policy archive`
preserves its journal. Prepare and approve a new program; earlier approvals and
test results never authorize the next revision. Source edits alone do not
establish a deployed release or production outcomes.

An already cached current reference remains unqualified for another common-program
cycle. Retained production windows are not yet the cache eligibility source.
Retrieval, compression, local model training, semantic cache, structured/agent
episodes and specialty/platform adapters keep separate execution contracts.
This program does not establish unified cross-engine selection for those paths.
The broader [market acceptance ledger](market-baseline.md) remains open.
