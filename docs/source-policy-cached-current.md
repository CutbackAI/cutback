# Improve a policy that already declares exact cache

This **v2 source path passed local and all 18 reviewed-head hosted gates**, then
merged in [PR 82](https://github.com/CutbackAI/cutback/pull/82). It is absent from PyPI
`0.1.0.dev5`. Use [progress](progress.md) for the exact tested source, installed
wheel, container and public CI evidence. The existing v1 program keeps its wire
format and refusal of a cached current reference.

V2 compares the current fixed synchronous Responses consumer using its observed
cache activation. It retains the complete current policy and its source hash.
The historical native anchor is never substituted for the current reference.
Async cache, streams, arbitrary wrappers and other optimization engines remain
separate adapters.

## Preparation

To inspect an independent synthetic application first:

```sh
cutback source-policy sample ./independent-cached-policy --cached-current
python ./independent-cached-policy/check_application.py
python ./independent-cached-policy/observe_application.py
```

Use the Python from the matching tool installation. The check exercises the
native consumer with a closed transport and excludes ambient runtime cache
configuration. Observation runs seven synthetic logical operations. With no
private cache scope, all seven invoke the closed native transport and the cache
remains configured but inactive. Telemetry stays disabled until explicitly
instrumented. The 160 synthetic cases contain eighty independent families,
each repeated twice with distinct IDs; duplicate inputs retain the same family.
The synthetic cache fee is a declared fixture bound, not a provider price.
`--async --cached-current` is refused before creating the example directory.

1. Commit the application, configure its source map and explicitly enable
   [policy runtime observation](policy-runtime-activity.md). Observation is off
   by default. Configure encrypted content rights separately for evaluation.
   Supply independently labeled cases, disjoint input families and a complete
   declared baseline task-cost snapshot as described in
   [the program walkthrough](source-policy-programs.md).
2. Use the matching source installation and collector. Inspect metadata:

   ```sh
   cutback source-policy inspect-activity --target . --producer app.py \
     --endpoint https://YOUR-COLLECTOR --project PROJECT_UUID \
     --environment staging --window-minutes 10
   ```

   The client discovers a service only if exactly one service matches the exact
   source commit, policy, workflow, call site, environment and time window. It
   considers at most twenty services. Missing, ambiguous or truncated results
   require current metadata or an explicit `--service SERVICE`; selection never
   uses recency. Inspection does not import the application or read its runtime
   cache file.
3. Prepare without sending a provider request:

   ```sh
   cutback source-policy prepare --target . --producer app.py \
     --endpoint https://YOUR-COLLECTOR --project PROJECT_UUID \
     --data-environment staging --runtime-window-minutes 10 \
     --purpose-reference improvement-review --max-usd 2 \
     --max-requests 1000 --max-wall-seconds 600 \
     --savings-per-success 0.001 --forecast-successful-tasks 100000
   ```

   Use `--runtime-service SERVICE` when several matching services exist.
   `--dataset UUID` and `--baseline-snapshot UUID` resolve deliberate data
   choices. No public example contains application credentials or a private
   runtime cache scope. Supply the scoped control token privately through the
   process environment.

Preparation requires positive, complete, uniform retained observations: every
operation has declared cache configuration, and all requested it or all ran
inactive. Failed, cancelled, mixed, empty, truncated or unknown operations do
not qualify. The observation window is one to thirty minutes; freshness expires
one hour after its start. This bounds the oldest possible sampled operation.
A recent query end cannot refresh old evidence.

The journal saves the exact window and summary identity. A retry reuses them;
it does not move timestamps or silently refresh approval evidence. The collector
rechecks the retained raw summary, current scope, content rights and source
before approval and dispatch. Late observations within the frozen window,
retention expiry or changes invalidate it before another call. An uncertain
billable call is never automatically retried.

## What the program measures

The current reference and automatic controls preserve the same cache
configuration and observed activation. Configured-but-inactive cache stays
inactive. A supplied different cache contract is refused; the identical contract
does not create a duplicate control. Generated instructions and a fitted router
retain that contract and activation too.

An active-cache comparison uses an **isolated cold cache** for the frozen
evaluation corpus. It does not copy or inspect a production cache. Both the
validation and untouched confirmation corpora must contain repeated inputs
that can exercise the incumbent cache. Otherwise preparation abstains before
spend. Do not manufacture duplicate cases to inflate independent sample counts:
repeated inputs belong to one family, and the quality gate still requires its
declared independent test families.

Conditional routing training explicitly disables caching for paired native
calls. That training reference retains the original encrypted policy identity;
only its execution activation changes. The reserved budget includes native
training, failed trials, generation, cache operations and the full confirmation.
Cache-hit historical usage is not charged again. Native bounds cannot be raised
by declaring large cache fees. Cache amounts remain declared upper bounds,
with unknown invoice verification, hosting costs, task-wide savings and capture
coverage.

The dashboard shows the observed state, retained count, freshness and comparison
scope. Viewer access exposes metadata; private policy content requires current
administrator rights and a purpose. The application agent prepares v2 so it can
bind the actual committed source. The dashboard inspects and approves that saved
program instead of reconstructing its runtime evidence from a template.

## Review and runtime activation

Use the same `continue`, `run`, `status` and `finish` commands as the main
walkthrough. One exact approval covers the bounded program. Finishing checks
both source copies, the exact configuration diff and its inverse rollback.

For an inactive incumbent, a retained cache configuration does not require
turning it on. For an active incumbent, the review reports the separate private
scope revision or deactivation requirement. Changing the policy hash cannot
silently reuse an earlier runtime authorization. The agent never reads, edits
or publishes that private scope file as part of source review. A checked diff
does not authorize deployment or establish production savings.

All automated acceptance uses independent synthetic applications, real local
PostgreSQL/encrypted cache and closed native SDK transports. It establishes
those mechanisms, not real model quality, customer savings or market readiness.
