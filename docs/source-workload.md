# Assess the workload your application actually sent

This unreleased source workflow connects a clean committed application to a
retained request-profile window. The agent derives the workflow, call site,
commit and native model, finds eligible completed analyses, and checks whether
the window justifies preparing a separate exact-cache trial. It makes no
provider calls or encrypted storage reads, and changes no source. Use a
source-matched installation and collector; PyPI dev5 lacks these commands.

## Try the independent application first

Follow [the local synthetic request-reuse setup](../examples/request-reuse/README.md),
then run:

```console
python examples/request-reuse/demo.py --endpoint http://127.0.0.1:8080 --source-workload
```

The driver creates and commits a temporary copy of
[this independent application](../examples/source-workload/app.py). Scanning is
parse-only. The driver explicitly executes only its own fixture using a fixed
synthetic native HTTP transport. Eight calls execute; the first four repeat.
The worker retains eight encrypted profiles and models three potential hits.
The agent discovers the matching analysis without a manually supplied ID.

Expect `paid_provider_calls: 0`, `application_source_changed: false` and
`source_workload.decision: prepare_separate_cache_trial`. The selected assessment
contains three modeled potential hits and exact decimal cost strings. The
temporary application and its ignored journal are removed. The separate
collector project is deleted by default. Use `--keep-project` to inspect it in
**Request reuse**, then delete it through project controls when finished.

This is synthetic software acceptance. It establishes no real model quality,
customer saving or production cache performance.

## Use authorized application evidence

1. Analyze the intended application with `cutback optimize /path/to/application --json`.
   Use `cutback optimize --help` for target/workflow selection. The source must be committed and clean.
   Unsupported source adapters decline.
2. Explicitly authorize [encrypted profiling](request-reuse.md), instrument the
   qualified SDK and retain a completed window of permitted application traffic.
   Follow the [reviewable instrumentation plan](application-onboarding.md),
   commit the reviewed integration, then run
   `cutback workflow refresh-source-map --target /path/to/application` and restart
   the observer. This writes only the private attribution map for the running
   commit; it neither imports application code nor enables capture. Regenerate
   it after source changes. Missing/stale attribution cannot qualify a source window.
   Run `cutback optimize /path/to/application --json` again after committing the
   integration so the saved workflow binds that same source revision.
   Profiling and encrypted reads require their own content/storage authorization;
   this command does not enable either automatically.
3. Save your approved exact-cache contract privately. It needs a freshness
   reference, TTL, literal content-retention/invalidation authorization, and a
   justified decimal upper bound per cache operation. Use the same approved
   freshness and TTL as profiling. A guessed cost cannot establish economics.
4. With the configured collector connection, run:

   ```console
   cutback workflow assess-workload --target /path/to/application --exact-cache-contract /private/cache-contract.json --environment staging
   ```

   Add `--workflow` if discovery finds several workflows. `--analysis-id` selects
   one completed analysis; its project, environment, source and workflow must
   still match. Credentials use the existing private connection or explicit
   endpoint/project setup. Never put credentials in command arguments.
5. Read `decision`, `reasons`, `selected_assessment` and
   `considered_assessments`. The ignored application journal retains at most five
   assessment results. No phase advances and no experiment is queued.

For the dashboard, export only bounded source metadata:

```console
cutback workflow workload-input --target /path/to/application --exact-cache-contract /private/cache-contract.json --output /private/workload-input.json
```

Open a completed **Request reuse** analysis, expand **Check this window against
your application**, select that private file, and assess trial eligibility.
The export excludes the request template, instruction and payload. The dashboard
keeps the file in memory and sends the explicit source binding and cache contract
to the project's administrator-only endpoint. It does not store the file in
browser storage. Treat source hashes and commits as private metadata.

## What can and cannot qualify

Only pinned plain synchronous OpenAI Responses application attempts qualify.
The analysis needs a complete bounded census, one source commit/call site/model,
provider-reported priced usage, approved freshness/TTL and positive modeled
native-window opportunity. A metadata digest binds canonical events, pricing
and coverage. Deletion, repricing, changed metadata and late-arriving attempts
invalidate the earlier census. Legacy analyses without this digest abstain.
Derived summaries expire no later than their earliest encrypted source.

For `N` attempts and `H` modeled hits, the declared operation bound is `2N − H`:
one lookup per attempt and a fill per modeled miss. The report subtracts that
upper cost from known modeled avoided provider cost. Money stays decimal text;
missing costs stay unknown. Unmeasured delivery latency and broader task/hosting
costs remain unknown. Cache behavior must still be validated.

Source lineage is client reported, not independent runtime attestation. A
positive hashed receipt grants no paid budget, quality claim, source-change
approval or production action. The agent rechecks source bytes after the request
and declines changed source or stale scope. It never runs repository hooks or
clean filters to establish the snapshot.

A positive decision supports preparing a separate trial only. Unified economic
selection across caching, routing, retrieval, prompts and training remains
unfinished. Fixture repetition cannot establish production hit rate, realized
savings or complete task economics.
