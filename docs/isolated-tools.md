# Isolated record tools

This source workflow evaluates an agent that reads a record, applies an approved
transition and verifies the resulting SQL state. It uses actual SQLite
transactions inside a separately contained Linux process. It cannot execute an
application's functions, arbitrary SQL, shell commands, URLs, browser actions or
plugins. [Run the independent sample](../examples/isolated-tools/README.md).

The feature is under qualification. PyPI `0.1.0.dev5` does not include it. The
sample's native model responses are scripted; its container, HTTP requests,
PostgreSQL intents, SQL effects, final-state checks and cleanup are real. Those
checks do not establish real-model robustness, customer quality or savings.

## Execution boundaries

The scanner stays offline and parse-only. The collector authorizes encrypted
datasets and immutable plans and exposes metadata receipts. The experiment
worker makes the already qualified synchronous native function turns and calls a
scoped remote tool service. Only the separately operated tool broker accesses a
local Docker daemon. Neither the collector nor the experiment worker receives a
Docker socket, registry credential or tool command execution endpoint.

Each broker is configured with one stable UUID, one locally installed image
digest and expiring project/principal/environment/workflow-scoped key hashes.
The broker's identity is bound into a `deterministic-v8` plan. Per-case grants,
record seeds, read fields and exact before/after transitions are frozen in the
encrypted dataset. The approver must separately authorize content use and any
state transitions. There is no automatic conversion from fixture episodes.

The broker validates the actual container before sending content. The runtime
checks its effective kernel profile and exercises denied network/root writes.
The profile requires a non-root UID, read-only root, no external network, no host
mounts/devices, dropped capabilities, seccomp, no-new-privileges, private IPC,
finite memory/CPU/PID limits, bounded temporary space, no retained container logs
and a finite wall clock. Image selection is an operator decision; model arguments
cannot choose an image, daemon, executable, path or endpoint.

The two tools are `records_read` and, with explicit write authority,
`records_patch`. The native patch schema carries a bounded `updates_json` object;
duplicate keys, nonfinite values, reserved fields and unapproved transitions are
refused. SQL predicates include project, principal, environment and resource.
Writes compare the current version and apply every approved field atomically.
Tool responses include only granted fields. An independent projection checks
visibility and effects before model delivery; the final SQL snapshot must also
match all projected changes and unchanged hidden/control records.

## Approval and operation accounting

Import a `cutback.isolated-record-episode.v1` dataset under a current synthetic
capture policy, then use the guided experiment form or `cutback tools prepare`.
The tool contract specifies project, record-owner UUID, stable broker UUID,
environment, exact image digest, session/call/result/time caps and explicit
content consent. It contains no token, endpoint or record payload. Default
planning authorizes zero dollars. Review and approve the ordinary exact plan
hash before queuing one execution. Provider requests still require an explicitly
enabled and credentialed worker.

The canonical database commits a session intent before initialization and an
operation intent before tool I/O. Native model requests keep their existing
separate pre-call money/request reservations. Uncertain starts/writes are not
recreated with new identities. Lost acknowledgements, missing final state,
revocation, cancellation, expiry or unconfirmed cleanup cannot produce a passing
task. An uncertain tool boundary stops further model requests. Known model
charges remain recorded. Tool cost and complete task cost remain `unknown`.

The dashboard and these CLI commands return metadata by default:

```text
cutback tools sessions RUN --project PROJECT --endpoint COLLECTOR --output sessions.json
cutback tools operations RUN SESSION --project PROJECT --endpoint COLLECTOR --output operations.json
cutback tools contain RUN SESSION --project PROJECT --endpoint COLLECTOR --approval-hash HASH --output containment.json
```

Containment queues cleanup; it does not assert that cleanup already happened.
Only a current administrator can request it. Purpose-authorized retained case
outputs use the existing encrypted evidence interface. Retired registered broker
keys can inspect their principal's metadata and contain its sessions, but cannot
start sessions, execute tools, replay content or read final state.

## Operating a separate broker

Use the source-matched installation with server dependencies. Run
`python -m cutback.tool_broker_boot --config /operator/private/broker.json` in a
separate service identity. Its strict configuration requires a stable nonzero
`broker_id`, `image_id`, scoped `clients` with SHA-256 token hashes and expiry,
and a TLS certificate/key for any non-loopback listener. Plain HTTP is accepted
only with an explicit loopback-only setting. Proxy headers and access logs are
disabled. A local process lock prevents two processes using the same broker UUID.
The Docker endpoint must be a local Unix socket or Windows named pipe; remote
TCP/SSH daemon contexts are refused. No image is pulled at tool-execution time.

The Helm chart's default-disabled `toolBroker` settings configure only an
external service and a dedicated worker-only token file. They never create a
Docker service or host socket mount. Cleanup can be enabled without payloads or
provider keys; execution additionally requires the ordinary experiment/content
opt-ins. Explicitly authorize the service destination in the worker egress
policy. The collector and migration job do not receive this scoped tool token.

On the experiment worker, configure `CUTBACK_TOOL_EXECUTION=true` plus
`CUTBACK_TOOL_BROKER_ORIGIN`, `CUTBACK_TOOL_BROKER_TOKEN`,
`CUTBACK_TOOL_BROKER_PROJECT`, `CUTBACK_TOOL_BROKER_PRINCIPAL`,
`CUTBACK_TOOL_BROKER_ID` and `CUTBACK_TOOL_BROKER_IMAGE_ID`. Use a scoped secret
delivery mechanism. Do not put these values in a repository, example, browser
form or screenshot. `CUTBACK_TOOL_BROKER_ALLOW_LOOPBACK_HTTP=true` is only for an
explicitly owned local demonstration. General model execution remains a separate
opt-in. No Docker privilege or provider key is forwarded to tool containers.

`CUTBACK_TOOL_CONTAINMENT=true` independently enables cleanup-only recovery.
It can use a retired scoped token and does not require model execution or
encrypted payload reads. Recovery attempts are leased and capped at three;
current administrators can deliberately request another finite set. There is no
inference or tool-operation replay in recovery.

After a broker restart, cleanup uses the exact stable broker/project/principal/
session labels, pinned image and policy digest. A failed daemon read is not proof
of absence. Confirmed containment creates a tombstone rather than restarting the
old session. The container's own deadline also bounds an abrupt broker outage.
For an outage that outlives these controls, leave the receipt uncertain until
the operator can confirm cleanup. Project deletion and destructive migration
downgrade refuse to discard uncontained execution evidence. Retention preserves
uncertain sessions. Closed broker/driver sessions retain policy metadata and
digests, not grant values, tool arguments, results or seeds.

## Limits

The current adapter is a fixed in-memory record database, not a general sandbox
for untrusted programs or a connector to production applications. A session has
at most 64 records, 100 operations, 120 seconds and a 64 KiB tool response. A plan
has at most 128 sessions and 4,096 total tool operations; the forecast must fit
the approved limits before execution. No stream/async SDK or modern-model
capability is added by this feature. Final-state assertions and model replies
remain scoped evidence; population coverage and production outcomes are unknown.

Versioned adversarial/benign utility suites, runtime application guardrails and
browser/computer agents remain separate roadmap work. See
[the market baseline](market-baseline.md) and [acceptance evidence](progress.md).
