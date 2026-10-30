# Guarded isolated record execution

This source increment connects the separate runtime checker to the isolated
record tool executor. It is under qualification and is absent from PyPI
`0.1.0.dev5`. [Independent sample](../examples/guarded-tools/README.md).

The `deterministic-v10` plan freezes an
`isolated-record-guarded-tools-v1` contract with the public checker binding,
record owner, project, environment, workflow, broker and image digest. Its guard
stages are exactly `tool_input` and `tool_output`. Existing v8 and v9 plans retain
their serialized bytes and behavior. A v10 tool policy does not claim protection
of model prompts, model replies or arbitrary production application functions.

The collector retains private rules in the explicitly authorized encrypted
store. The separate checker receives private policy provisioning through an
anonymous pipe or its own encrypted bundle. It receives no collector database,
model-provider or project encryption key. The worker receives only a public
binding and an expiring scoped checker credential. The separate tool broker
retains its existing Docker authority; the collector and worker never receive
the Docker socket.

## What a blocked response means

1. Reserve a canonical tool-operation identity and check current authority.
2. Check the tool name and decoded arguments before broker dispatch. A denied
   input records `DENIED / NOT_STARTED / WITHHELD` with no state change.
3. Commit `STARTED` only after the matching canonical input check permits it.
4. Dispatch once, preserve the broker receipt and independently verify its
   authorized effect.
5. Check the complete broker response before releasing it. A denied response
   preserves `RETURNED`, its known state change and `WITHHELD`. It stops further
   native model turns. Withholding cannot undo a committed write.
6. An allowed response records `AUTHORIZED`. This is permission to release the
   reply; it does not prove another model consumed it. Final SQL state and
   confirmed containment are still required.

A lost broker/write acknowledgement remains `UNCERTAIN`, with unknown state
change and `UNKNOWN` dispatch/delivery evidence. The worker stops and does not
replay the operation or queue another execution. Known native model charges stay
recorded. Tool cost and complete application cost remain unknown.

Explicit fail-open permits operating through checker unavailability. Such an
episode cannot pass the joint guarded qualification: business-contract evidence
is retained separately, guard coverage is incomplete and the joint score is
unknown. Neither fail mode overrides current consent, project/owner scope,
approval, retention, canonical intent or shared check quotas.

## Operator configuration

The experiment worker needs its existing scoped broker configuration plus:

| Setting | Value |
| --- | --- |
| `CUTBACK_TOOL_GUARDRAIL_BINDING_FILE` | Public JSON binding matching the approved v10 plan |
| `CUTBACK_TOOL_GUARDRAIL_ORIGIN` | Fixed TLS checker origin |
| `CUTBACK_TOOL_GUARDRAIL_TOKEN_FILE` | Separately protected file containing the scoped token |
| `CUTBACK_TOOL_GUARDRAIL_TOKEN` | Alternative in-memory environment credential; never configure both sources |
| `CUTBACK_TOOL_GUARDRAIL_ALLOW_LOOPBACK_HTTP` | `true` only for an explicitly owned literal `127.0.0.1` development checker |

Binding/token files are bounded and symlinks are rejected. Private rules do not
belong in the binding file. The worker verifies exact identity and continuing
approval before native requests; every actual tool-stage check uses a durable
shared quota intent. There are no HTTP retries.

In the dashboard, select an isolated record dataset, enable checks before tool
calls and reply release, and supply the approved tool policy ID. Planning makes
no provider calls. Review the frozen budget and exact plan hash before approving
one run. Alternatively, pass the explicit guarded contract to `cutback tools
prepare`; its default budget is zero.

Operation receipts show broker intent, known state changes and reply release
separately. Metadata exports contain no tool arguments, responses, literal rule
content or checker credential. Older contracts display unknown guard evidence
rather than reconstructed decisions.

## Limits

Rules use the existing bounded literal, structural JSON and tool-name checks.
These are not semantic jailbreak classifiers. The target remains the two scoped
record operations. Broader security-suite targets, adaptive attack generation,
general false-positive rates, production tool adapters and real-customer
validation remain separate work. The synthetic sample proves execution and
failure handling, not real-model robustness or customer savings.
