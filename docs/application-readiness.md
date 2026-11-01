# Diagnose an application before connecting it

This source-only command passed local component qualification and is absent
from PyPI dev5. Exact-head hosted acceptance remains pending. The
[receipt](evidence/application-readiness-acceptance.json) records both installed
SDK selections, the independent core-only installation, actual offline non-root
image and ownership-safe cleanup/path guards, including prior source failures
and Windows privilege skips.
Run it from the intended application root:

```sh
cutback workflow diagnose --target .
cutback workflow diagnose --target . --json
```

Diagnosis refreshes bounded offline discovery, saves the normal private agent
journal, and presents one decision report. It parses application source and
tracked check declarations; it never imports the application, collects tests,
contacts the collector, loads provider credentials or runs inference. Application
files and dependency pins are unchanged. Metadata can identify application files
and workflows, so keep it in that application's ignored workspace.

The report brings together five questions:

| Question | Evidence and next step |
|---|---|
| Can Cutback preserve this request? | Existing qualified source adapters and fixed request diagnostics. Unsupported options, preprocessing, destinations or storage behavior require inspection; their values are omitted. |
| Which workflow should the agent prepare? | Bounded workflow IDs and concrete argument vectors for narrowing discovery. A fixed policy consumer keeps its whole-program path instead of replaying an inner SDK call. |
| Does this application have regression checks? | Clean tracked source and bounded candidate check declarations. Counts/runtimes/origins are metadata; command bodies are omitted. Candidates still require dependency/side-effect review and execution against both source trees. |
| Where should observation start? | Existing instrumentation-plan status and explicit plan-only argument vectors. A fixed policy consumer requires startup outside that wrapper. Process/handler coverage needs application inspection. |
| What decisions are still needed? | Permitted data and rights, approved collector/current project access, quality/holdout definition and request/USD ceiling. Local fixture discovery does not authorize data use. |

`REVIEW_PREPARATION_INPUTS` means a static adapter is available, **not** that
inference, capture, source changes or deployment are approved. The report gives
the matching preparation command's help and the decisions needed before using
it. The current tool interpreter's package versions are shown separately from
the application's installed SDK versions, which remain unknown. Inspect the
application's actual pinned installation before enabling an observer.

`SOURCE_ADAPTER_REQUIRED` explains detected blockers without rewriting required
application behavior. `SELECT_WORKFLOW_OR_ADAPTER` asks for one bounded source
scope. Missing/dirty Git roots, unsafe paths and malformed check declarations
report a review requirement. Preserve your work; diagnosis never resets Git or
silently excludes an unsafe file. No candidate tests means coverage is unknown.

If a plan/run already exists, `CONTINUE_EXISTING_WORKFLOW` points back to its
saved intent. A changed source tree does not replace that intent, discard its
approval or retry an uncertain billable operation. Reconcile the old operation
through its ordinary workflow before starting a new one.

Argument vectors in JSON are arrays for an agent or process API, not shell
snippets to concatenate. They are never automatically executed. Reports include
an analysis hash and a report hash; both identify locally reported metadata,
not independent CI attestation or complete runtime coverage.

The image acceptance checker uses an exact local image with pulling disabled,
read-only root, bounded tmpfs/memory/process/CPU limits and no network. Its fixture
code travels over stdin. It confirms cleanup after a lost running client and
refuses to remove a same-named container with different ownership. Cleanup uses
the inspected immutable container ID. A separate daemon deadline bounds fixture
execution if the host disappears; expiry was not separately measured in this
component's tests.

## Independent first-use check

Use the existing synthetic sample in a separate new directory:

```sh
cutback sample independent-support
cutback workflow diagnose --target independent-support
```

The native request should have a qualified profile. This generated sample has
no independently declared regression runner or clean Git root, so the check
section must remain a review requirement. No provider key is needed for this
offline step. The [source-policy sample](source-policy-programs.md) demonstrates
a separate fixed consumer and application checks with closed native transports.
Neither example proves real task quality or savings.

For the complete permitted-data and reviewed-improvement journey, follow
[application onboarding](application-onboarding.md) and the [agent workflow](agent-workflow.md).
