# Shared cost and reliability charts

This source feature passed local component qualification and is absent from
PyPI dev5. It adds saved metadata charts to **Costs → Saved charts**. Both
fresh installed SDK suites, the HTTP-only client, authenticated browser/mobile/axe
and full image/database/encrypted-object recovery passed. [The receipt](evidence/shared-analytics-acceptance.json) binds the measured runtime; [progress](progress.md) retains earlier failures. Corrected hosted acceptance is pending.

Editors can create a dashboard from the application-health template, choose an
existing project environment and a period, then add up to eight charts. Viewers
can open the same saved definition. Each update uses the reviewed revision;
another editor's change produces a conflict instead of a silent overwrite.
Deleting a definition is also revision-bound. Retrying an old save cannot
recreate a definition that was explicitly deleted.

Available metrics are observed attempts, known modeled cost subtotal, attempts
marked error, p95 duration and provider-reported input/output-token subtotals.
Charts can group by UTC day, model, provider, workflow or traffic purpose, with
optional workflow and purpose filters. Line charts require time grouping. Stat,
bar and exact table presentations are also available. These definitions contain
typed choices, never SQL, scripts, credentials or customer payloads.

## Read the evidence correctly

All panels in one read share an environment, UTC window and consistent database
snapshot. They reuse the canonical usage calculation: retained native attempts
take precedence over exact external mirrors, recorded costs are not repriced,
and application traffic can be separated from evaluation/judge/optimization
traffic. The default period includes the current UTC day; it does not predict
the rest of that day.

- Known cost is a subtotal. Unpriced attempts keep the full modeled total
  unknown; neither number is an invoice or verified savings.
- Missing days remain gaps. No observations do not establish healthy traffic or
  complete population coverage.
- A retained single-day p95 can remain available. A missing combined quantile
  cannot be rebuilt by averaging daily p95s. Partial retained days are excluded
  and explicitly mark the selection incomplete.
- Error counts are execution metadata, not independently verified failed tasks.
  Provider-reported token subtotals exclude missing/estimated usage.
- Plot coordinates use an approximate visual scale. Exact decimal strings and
  observed/priced/usage/duration denominators remain in the table and JSON export.

Definitions are limited to 100 total saved views per project, shared with
investigation views. Data reads share a 60-per-minute project quota across
credentials. Each read has at most eight panels, a 365-day window, 365 time points
or 100 dimension groups, and a 50,000-row retained-summary bound. Unique query
shapes are reused within the read. SQL statements have at most five seconds,
reduced by a 15-second read deadline; storage or bound failures produce an error,
never stale replacement charts. These controls are not a production capacity SLA.

Current project access applies to every read and mutation. The browser keeps
credentials in memory and stores no dashboard token in URLs, local/session
storage or cookies. Definitions and metadata participate in project export and
deletion. Project erasure also removes its analytical quota record; compatible
project read locks prevent an in-flight chart read from recreating it afterward.

Migration `0037` adds the saved-view project/environment constraint. It preserves
earlier migrations, refuses invalid legacy scopes rather than repairing them
silently, and refuses downgrade while analytical definitions remain. Export and
explicitly remove those definitions before binary/schema rollback.

## Try it independently

Run the ordinary [support-desk demo](../README.md#run-one-complete-example). It
now creates `support-cost-and-reliability` over its already observed synthetic
native calls. No additional fabricated usage or inference is needed for the
charts. Choose **Costs → Saved charts**, then that dashboard.

For an existing local collector, the [standalone example](../examples/shared-analytics/README.md)
previews offline, uses an explicitly selected current editor key, and journals
one save/read intent. It is HTTP-only and imports no provider/server SDK. Its
completed receipt is explicitly historical; rerunning it makes no request and
never recreates a deleted definition.
