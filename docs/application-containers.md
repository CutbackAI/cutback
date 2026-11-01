# Run reviewed application checks in local containers

This source feature passed local qualification and all 18 reviewed-head hosted
jobs, then merged in [PR 82](https://github.com/CutbackAI/cutback/pull/82).
It is absent from PyPI dev5. Its v2 contract extends
the [portable application checks](application-checks.md); the v1 local runner
and its serialized contracts remain unchanged. See [progress](progress.md) for
the actual acceptance and publication state.

First discover or prepare the two exact source trees using the existing
[independent example](../examples/application-checks/README.md). Review its
commands. Then select an already installed, trusted Linux image by its exact
local `sha256:` image ID. Building or pulling an application's dependency image
is a separate reviewed operation; Cutback does neither automatically.

```sh
cutback checks isolate contract.json --image-id sha256:YOUR_64_HEX_IMAGE_ID \
  --python-path /usr/local/bin/python --output isolated-contract.json
cutback checks run isolated-contract.json BASE_ROOT PROPOSED_ROOT \
  --execute-local --output receipt.json --junit junit.xml
cutback checks verify isolated-contract.json receipt.json BASE_ROOT PROPOSED_ROOT
```

`checks isolate` only writes the contract. It does not contact Docker or execute
the image. `checks run --execute-local` is the explicit execution boundary. A
missing image/runtime, remote daemon, unsupported limit or changed source stops
the operation; there is no fallback to host execution, image pull or inference.
Use `/app/.venv/bin/python` when checking with the matching collector image.
Node commands use `/usr/local/bin/node` by default; `--node-path` can bind the
reviewed image's absolute runtime path. These paths and limits enter the frozen
contract and receipt. Dependencies must already exist in the chosen image.

## Containment contract

The runner requires a local Linux Docker daemon with memory/PID limits and the
built-in seccomp profile. It accepts local Unix/named-pipe endpoints, never a
TCP daemon. It checks image identity and refuses image-declared volumes before
creating a container. Each command gets a new container with:

- UID/GID 10001, all capabilities dropped and no new privileges;
- network disabled, a read-only root and no ports, devices or Docker socket;
- one CPU, 512 MiB memory without additional swap, 64 processes and a bounded
  64 MiB temporary filesystem mounted with `noexec,nosuid,nodev`;
- a read-only private copy containing only the exact frozen tracked source;
- a freshly cleared application environment plus reviewed fixture variables;
- no application stdin or retained stdout/stderr, and Docker logging disabled.

The v2 JSON contract can explicitly bound memory to 128–4096 MiB, processes to
16–256 and temporary storage to 8–256 MiB. Command time/output limits retain
their existing bounds. The created configuration is inspected before startup.
Runtime binary hashes come from a separate invocation of the same image, rather
than parsing application output. The owned container is forcibly removed after
success, failure, output overflow, timeout or cancellation, including detached
descendants. A lost Docker control reply cannot create a passing receipt.

An independent GNU `timeout` process also bounds the command inside its private
PID namespace, with a one-second termination grace period. The reviewed image
must include `/usr/bin/env` and `/usr/bin/timeout` as well as the selected runtime;
missing utilities cause refusal, never host fallback. Killing the complete host
runner tree cannot leave the application running beyond that container deadline.
A host crash can leave a stopped container and private temporary source copy.
The container label `org.cutback.application-check.contract` records only the
frozen contract hash so an operator can identify that attempt for cleanup. A
missing check receipt still requires operator review and grants no replay.

Git history, ignored files, the application's private Cutback workspace, home
directories and host credentials are not mounted. Tests may write temporary
fixtures under `/tmp`; they cannot change tracked source or persist output to
the host. Checks requiring network, mutable source, Git history, host databases
or additional mounts do not fit this contract. Use a separately reviewed test
arrangement for those checks; this runner does not grant those permissions.

The image and local daemon remain trusted. A container shares its host kernel;
this is not a virtual machine or independent execution attestation. Image
contents and preinstalled dependencies are bound by the exact image identity,
but Cutback does not certify their provenance. A passing synthetic check cannot
establish model quality, complete application coverage or production safety.

## Automatic checked handoff

The native campaign, routing program and source-policy program `finish` commands
accept `--container-image IMAGE_ID`, `--container-python-path PATH` and
`--container-node-path PATH`. For example:

```sh
cutback source-policy finish --target . --workspace ../private-check-handoff \
  --purpose-reference improvement-review --container-image sha256:YOUR_64_HEX_IMAGE_ID \
  --container-python-path /app/.venv/bin/python
```

The preview freezes the image with the existing exact source/command/rollback
intent. Review the discovered commands and supply coverage and
`--execute-local` using the same options. Both source trees run inside that
container contract before review. Resuming with another image or removing the
container option changes the frozen intent and is rejected. Current content
rights and confirmation are refreshed after checks. A completed receipt is
reused; a dispatched check with a missing reply is never silently repeated.
Neither these options nor a passing check authorize publication or deployment.
