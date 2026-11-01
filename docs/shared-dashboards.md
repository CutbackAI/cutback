# Shared team dashboards

This source feature passed local component qualification and is absent from
PyPI dev5. Public publication and exact-head hosted acceptance remain separate.
It composes
saved trace and incident metadata views for one project and environment. Current
qualification and publication gates are recorded in [progress](progress.md).
[Encrypted incident comments](incident-comments.md) have a separate source
implementation under qualification. Arbitrary chart queries and enterprise
provisioning remain separate work.

## Build a dashboard

The independent `cutback demo` creates **support-team-overview** from its actual
retained synthetic spans. Open **Monitoring** and choose that dashboard to try
the complete read path without setting up new traffic or a provider key.

1. Connect to the collector and choose **Monitoring**. A viewer can open shared
   dashboards; an editor or project administrator can create, update and delete them.
2. Open **Save a trace view for your team**. Enter a stable key and a configured
   environment. Workflow, service and release filters are optional. Leave them
   blank to inspect retained spans across that environment. Save the view.
   The existing **Save incident selection** form saves an incident view instead.
3. Open **Compose a shared dashboard**. Choose its key and environment, select
   one to eight views, review their revisions and filters, and create it.
4. Open the saved dashboard. Trace panels show actual retained span metadata;
   incident panels show incidents whose evaluation rule filters match the saved
   selection. Their populations differ. Neither panel infers independent task
   success, healthy traffic, complete population counts or latency quantiles.
5. Use **Inspect trace** or **Inspect incident** to investigate a retained record.
   The dashboard's trace investigation is metadata-only. Authorized content reads
   remain in their separate purpose-scoped workflow.

Panels have bounded pages. **Load more panel records** continues the same pinned
composition revision. A dashboard edited between requests causes a revision
conflict; records from different definitions are never silently appended.

## Review a teammate's change

A dashboard copies each view's exact typed filters and revision when it is saved.
Changing or deleting the original view does not retarget a panel. **Refresh
dashboard** reports whether each source is current, changed or deleted, while
retaining the pinned selection. Recreating a view ID with different filters is
also treated as a change.

Choose **Refresh saved views**, then **Edit dashboard composition** to review
current revisions. Remove unavailable source views or choose replacements,
review the filters, and save the next dashboard revision. A teammate's concurrent
edit or a stale deletion returns a conflict instead of overwriting their work.
The dashboard key stays stable; use a new dashboard to choose a different key.

The store retains the current composition and an audit event for each successful
update/deletion. It does not provide a browser for previous composition bodies.
The definition hash identifies the current saved definition; it is not an
independent authenticity attestation.

## Scope, storage and limits

- The existing saved-view store holds these typed metadata definitions. The
  combined project limit is **100 saved views and dashboards**, not 100 of each.
- A dashboard has **one environment**, **one to eight distinct leaf views**,
  no nested dashboards, and no executable SQL or arbitrary query code.
- Trace queries apply workflow/service/release/kind filters on the server.
  Incident queries match the declared evaluation rule filters and incident state.
  Unknown coverage remains unknown; a filtered page is not a population count.
- Viewer/editor rights are checked on every request. Revoked credentials cannot
  read the next page. Ingest-only credentials cannot read dashboards.
- Project export and deletion include the definitions. Audit events contain IDs,
  revisions and panel counts, with no captured application payloads.
- Use matching collector/dashboard versions. An older collector does not
  understand the new dashboard definition variant; a binary downgrade requires
  exporting and removing dashboard definitions first. Released migrations are
  unchanged by this metadata-format addition.

API clients use `POST /v1/projects/{project_id}/dashboards` with a stable
`dashboard_id`, key, environment, expected revision and explicit source-view
IDs/revisions. List results use bounded UUID keyset pagination. Panel reads
require `expected_revision`; updates and deletion also require the reviewed
revision. Scoped credentials belong in authorization headers, never URLs or
stored dashboard definitions. See the generated OpenAPI contract for the exact
typed schemas.
