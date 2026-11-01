# One budget for a source policy

This source feature is being qualified. PyPI `0.1.0.dev5` does not contain it.
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
3. Freeze a preview. Substitute your own existing IDs, endpoint, declared
   opportunity assumptions and maximum spend:

   ```sh
   cutback source-policy prepare --target . --producer app.py \
     --endpoint https://YOUR-COLLECTOR --project PROJECT_UUID \
     --dataset DATASET_UUID --baseline-snapshot SNAPSHOT_UUID \
     --purpose-reference improvement-review --max-usd 2 \
     --max-requests 200 --max-wall-seconds 600 \
     --savings-per-success 0.001 --forecast-successful-tasks 100000
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
