# Portable local application checks

Source-checkout functionality, separate from scanner, collector and model worker. [Independent runnable example](../examples/application-checks/README.md). It does not execute application code during scanning or in a shared server.

Shared-change planning and handoff coverage drafts passed [local component
qualification](evidence/application-check-planning-acceptance.json): 131 checks
per pinned SDK, zero skips, plus core-only and non-root image walkthroughs.
They are source features, absent from PyPI dev5; current-head hosted acceptance
remains pending. This does not establish semantic coverage of a real application.

An opt-in [local container contract](application-containers.md) passed local
component qualification for checks requiring a network-disabled, read-only
source environment. The v1 process runner below retains its explicit limits.

## Discover existing checks

Working source adds `cutback checks discover BASE_ROOT PROPOSED_ROOT --output contract.json --report discovery.json`. Both outputs must stay outside the frozen source roots. It prepares the ordinary executable contract and a separate hashed discovery report without requiring handwritten command JSON. Discovery inspects tracked configuration and guarded scripts, imports no application, runs no test collection or package script, and grants no model-call authority. Review the commands before the existing explicit local execution step.

An explicit `[tool.cutback.application_checks]` table in tracked `pyproject.toml` can contain `commands = [{key = "application-suite", arguments = ["-m", "pytest"]}]`, using the same bounded command schema as manual preparation. Otherwise discovery proposes the whole pytest suite when tracked Python tests or pytest configuration exist, a conventional guarded `check_application.py` when no pytest suite exists, and native Node tests only for the exact `"test": "node --test"` package script. It does not translate arbitrary npm/shell scripts. Missing dependencies, plugins, fixture effects, ignored runtime state and external consumers stay unresolved. Test/config/runner definitions must match in both trees; an altered test runner requires explicit separate review.

Full-suite discovery deliberately does not claim that an import graph or a file-name heuristic proves affected-workflow coverage. The report identifies candidates and their source, not semantic coverage or independent attestation. A workflow-to-command coverage assertion in a campaign still requires the authorized agent/owner to inspect the actual checks. The campaign sample discovers its existing native check automatically and executes it against both exact workspaces. A passing local command retains the same trust limits as manual preparation.

## Plan checks for shared changes

The source build adds a parse-only impact plan to discovery. Without additional
configuration it retains every candidate command. With a reviewed workflow
declaration, it resolves static Python imports in root and `src/` layouts against
**both** frozen trees, including transitive imports and package initializers.
Changed shared modules and explicitly declared prompt/configuration resources
identify affected workflows. No application imports, test collection, dependency
installation, network requests or model calls occur during planning.

Keep the ordinary command declarations above and add a separate table:

```toml
[tool.cutback.workflow_checks]
version = 1

[[tool.cutback.workflow_checks.workflows]]
key = "triage"
entrypoints = ["triage.py"]
resources = ["prompts/triage.txt", "settings.json"]
commands = ["triage-full", "triage-guard"]
regression_commands = ["triage-guard"]
```

Declare each workflow separately; shared resources may appear in several
workflows. All paths are exact tracked paths in both trees, with Python
entrypoints. Command keys reference the existing bounded command declarations.
Every workflow needs at least one regression command drawn from its commands.
Workflow/configuration and check definitions must remain identical across both
trees. Adding/changing this declaration is a separately reviewed setup change,
not part of the application change it is intended to assess.

Default discovery keeps **all** commands and puts affected workflow commands
first. An explicit `--select-affected` selects all commands for affected
workflows, the declared regression guards for every unchanged workflow, and
every command not assigned to a workflow. Shared commands are run once per tree.
It never omits an unchanged workflow entirely. The separate hashed discovery
report records changed/dependency paths, affected status, selected/omitted keys,
global checks and fallback reasons. The output remains an ordinary v1 executable
contract, compatible with the existing process/container runners and verifier.

Missing declarations, unowned changed paths, recognized dynamic import/execution
or reflection constructs (including references assigned to aliases),
runtime import-path/module-hook access,
wildcard imports, invalid Python, ambiguous/unsupported module layouts, exceeded
analysis bounds or no changes retain **all** commands. Invalid declarations,
unknown command references and missing declared paths refuse discovery instead
of silently dropping checks. Graph inspection is bounded to 8 MiB of Python
source, 500,000 syntax nodes and 50,000 edges per tree. Each workflow lists up to
1,000 dependency paths; truncation is explicit and forces the full set of checks.
Existing source/configuration and command bounds still apply.

This plan is based on reviewed declarations and bounded static reachability.
Arbitrary dynamic application behavior, non-Python consumer graphs, undeclared
resources, external consumers and semantic test coverage remain unknown. The
agent/owner must review whether a command actually detects a bad change, and
whether an unchanged workflow's guard is sufficient. Choosing selective checks
does not create independent coverage attestation or permission to run them.
Reports contain application path metadata; keep a real application's reports in
its private workspace rather than publishing them in this product repository.

[Run the independent good/broken shared-consumer example](../examples/workflow-checks/README.md).

### Use the mapping in an agent handoff

The qualified source also prepares `coverage-candidate.json` during a campaign or
source-policy handoff when every required workflow ID exactly matches an
affected workflow in the tracked declaration, every mapped command is in the
frozen check contract, and impact analysis has no fallback reason. Use the
workflow IDs from `cutback workflow explain`; descriptive labels in an unrelated
sample do not identify your application workflows. This draft removes manual
JSON assembly; it does not assert independently verified coverage.

The normal `finish` preview returns the candidate's path/hash and marks it as a
tracked declaration requiring explicit review. Read the actual checks and draft,
then pass that path to the existing `--coverage` option with `--execute-local`
when execution is authorized. The preview still reports
`owner_consumer_coverage_required: true` until coverage is explicitly supplied.
It does not silently accept its own suggestion or execute checks. A retained
candidate is hash-bound to its handoff journal; changing or losing it refuses
resume rather than silently replacing reviewed evidence. Use a separate explicit
coverage file to propose a different mapping.

Missing/mismatched IDs, unchanged declared scopes for a required affected
consumer, unknown impact, undeclared commands and handoffs without a finite
required-workflow set retain the existing explicit coverage path. An explicit
`--commands` override also retains manual coverage. Current collector authority,
one-shot dispatch, source/rollback checks and native-call accounting are
unchanged. Full semantic coverage and independent application CI attestation
remain separate requirements.

`checks prepare` freezes two independent Git roots and explicit commands. Each source binding includes the commit, tracked file names/content hashes and deterministic tree hash. Inspection uses Git plumbing without `status`/`diff`, clean filters, fsmonitor, inherited global configuration or permitted transport protocols. Source roots must be actual repository roots without symlink traversal; submodules/symlink files, changed index/working source, untracked files, overlapping roots, more than 10,000 files, 16 MB per file or 64 MB total are rejected. UTF-8 CRLF checkouts may match LF committed blobs; receipts still bind the exact raw bytes. Custom clean-filter transformations are not applied. The scope is tracked Git source, not ignored files, installed dependencies, network services or all possible runtime state. Freeze/check dependencies separately in CI; a source hash alone cannot prove the runtime was reproduced.

Each command declares a stable key, `python` or `node` runtime, argument list, working directory inside the source root, fixture environment, timeout and output bound. No shell is inserted. Limits: 20 commands, 32 arguments of 2 KB each, timeout 1–300 seconds and combined output bound up to 1 MB per invocation. Commands run once for base and once for proposed. Sources are checked before execution and after every command. A changed tree fails the check; a stale tree prevents execution. Runtime binary hashes enter the result.

`checks run ... --execute-local` is the explicit execution boundary. The child inherits no provider, cloud, collector, home, proxy or user module environment. Only declared `CUTBACK_FIXTURE_*` values (excluding credential-like names), required Windows system paths and narrowly constructed runtime settings are passed. Standard input is closed. Output is counted and discarded rather than retained or included in errors. Timeout/output overflow terminates the owned process group/job, including descendants; cancellation also cleans up. This is process containment and environment minimization, **not an OS/network/filesystem sandbox**. An approved application command still runs with the current account's permissions. Use disposable CI/development workers without credentials, sensitive mounts or production access.

The runner emits metadata JSON plus JUnit with one base/proposed command result. Nonzero exits, timeouts, output limits, source changes and unfinished child output are failures. It exits one for a completed failed comparison. Preparation/contract errors exit two. Reports identify commands/statuses and bind the contract, both trees, runtime hashes and execution time. They do not retain raw assertions, application output, provider data or secrets. Store detailed test logs only under an application's separately authorized private controls.

`checks verify` validates receipt integrity, the declared result set and current source bindings. Hashes detect accidental/stale/tampered artifacts relative to the supplied contract; they are **not independent authenticity attestation**. A party controlling the contract and receipt can fabricate both. Label these results externally executed local checks. CI signing/attestation and dependency/environment qualification are separate stronger trust controls. Passing these checks does not independently establish semantic model quality, production safety or customer savings.

For a PR, check out the exact base and proposed revisions into separate clean workspaces, install pinned dependencies, prepare or verify the reviewed contract, explicitly run checks and upload JSON/JUnit as CI artifacts. Set checkout `persist-credentials: false`, use read-only workflow permissions and do not expose secrets to untrusted PR code. Keep generated reports outside either source root. Source-patch rollback continues to use its exact patch receipt; registry alias rollback has its own immutable receipt. Neither runner deploys changes.
