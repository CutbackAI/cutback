# Recover local project and credential setup

This source extension is under qualification and is absent from PyPI dev5.
It covers local project and scoped credential creation before encrypted example
preparation. It makes no provider requests and cannot approve an experiment.

Run the ordinary, explicitly authorized `workflow prepare --local` command with
your original permitted example, rights and budget options. Before its first
project write, the agent saves a private intent in
`.cutback/agent/bootstrap-credentials.json`. It contains a stable project UUID,
two key UUIDs, privately generated credentials, the collector destination and
owner, the application-root fingerprint and one fixed one-day expiry. It is a
credential file: keep it ignored and protected by the operating system's account
and file permissions. Never copy it into reports, issues or other applications.

If setup reports `BOOTSTRAP_COLLECTOR_UNAVAILABLE_RESUME_SAVED_INTENT`, restore
the same collector and rerun that original `workflow prepare --local` command.
The agent reuses the saved project and credentials. A reply lost after a commit
does not create another project, mint replacement keys or renew the expiry.
Once both key acknowledgements match, the agent atomically saves the normal
private `connection.json` and removes the pending credential file. The connection
receipt binds the original project, keys, credentials, destination and root;
subsequent use rechecks that binding and expiry before a request.

This initial connection recovery precedes the workflow's scan, rights and
dataset journal. Once status reports `RESUME_SAME_NONBILLABLE_PREPARATION`, use
`cutback workflow resume-preparation --target .` for those later steps. An initial
connection interruption still requires the original preparation command because
its example/rights/budget options have not been frozen yet. Neither recovery
path approves or queues inference.

For `BOOTSTRAP_IDENTITY_RECONCILIATION_REQUIRED`, stop and inspect the actual
scoped records with the operator. An unexpected receipt is not accepted as proof
of creation. Do not erase the journal, change IDs or create another session to
bypass the conflict. Changed owner/destination/root, modified intent, expired or
revoked keys, deleted projects and unsupported collector protocols also stop.
Expired authority needs a separately reviewed replacement; retry never extends
it. Existing legacy connections and explicit remote project/token setup retain
their documented behavior.

## Collector contract

Authenticated readiness advertises `cutback.nonbillable-bootstrap.v1`.
`POST /v1/projects?request_id=UUID4` acknowledges the same original project only
for the same normalized request and current authority. Existing project-deletion
audit receipts prevent recreating that identity.

`POST /v1/projects/{project_id}/scoped-keys` takes an exact UUID4, role,
environment, absolute timezone-aware expiry and a privately generated URL-safe
credential. The collector stores only its hash. The response contains the key
and project IDs, scope, fixed expiry and project-bound request fingerprint; it
never returns the credential. Replaying an identical request rechecks current
rights, expiry/revocation and retained creation history. Changing any bound
field rejects. Initial expiry must be within seven days; local setup uses one.
The new and legacy issuance APIs share one project lock and a retained-key quota
of 5,000. Replaying a retained valid key consumes no additional slot.

This API is a setup protocol, not a recovery mechanism for billable operations.
An uncertain inference charge remains unsafe to replay automatically. No schema
migration or wider provider compatibility is implied.
