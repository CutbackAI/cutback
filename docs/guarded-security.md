# Evaluate security and useful work with an active tool guard

This source workflow combines the paired record-security suite with actual
runtime tool checks. It is under qualification and is absent from PyPI
`0.1.0.dev5`. [Run the independent sample](../examples/guarded-security/README.md).

An explicit `deterministic-v11` plan freezes both the reviewed suite identity and
the exact public guard binding. Its episode version is
`native-isolated-record-guarded-security-v1`. The existing guarded record
contract still limits execution to 128 isolated sessions and 4,096 tool calls;
it does not inherit the larger unguarded suite limits. Existing v9 security and
v10 guarded-task approvals retain their contracts and cannot silently become
combined evaluations.

The model, suite cases, guard policy, record owner, project, environment, workflow,
broker, image and budgets are frozen before approval. The worker uses the same
one-shot model and tool journals, continuing consent checks and scoped separate
checker as the [guarded tool workflow](guarded-tools.md). This guards tool inputs
and broker replies, not arbitrary model prompts or production application tools.

## Read the evidence separately

| Evidence | Meaning |
| --- | --- |
| Declared attack success | An exact canary appeared in observed model output or the scoped SQL boundary was violated |
| Business utility | The separately declared task completed; refusing or stopping unfinished work cannot pass it |
| Policy decisions known | Observed checks allowed the operation or returned a known rule denial; checker unavailability cannot satisfy this |
| Complete check coverage | Every configured check completed; a known denial may stop evaluation early, leaving this incomplete |
| Benign family interruption | The guard stopped an independently declared benign task; repeats share one family denominator |

A known guard stop with a verified final SQL snapshot and containment can
establish that the declared attack did not realize in that stopped execution.
It also establishes that the required completed business task did not finish.
An outage, unknown effect, missing snapshot or unconfirmed cleanup cannot supply
those conclusions. A blocked broker reply still cannot undo its recorded write.

The report never converts incomplete check coverage into complete coverage. A
known short-circuit policy decision and remaining unchecked rules are distinct
facts. Explicit fail-open may finish useful work during a checker outage, but
unknown policy decisions prevent joint guarded qualification. Missing retained
coverage and check totals remain unknown; a known check subtotal is labeled
separately.

Benign interruption is an observed task-family metric, not a general classifier
false-positive rate. The latter remains unknown without independently qualified
labels and population evidence. Small samples, missing attack categories and
unknown families retain the original conservative security/utility gates.

## Prepare and review

Import a complete reviewed paired-suite manifest under an authorized capture
policy. Register and approve a tool-only guard matching the same owner, project,
environment and workflow. In the dashboard's experiment form, select the suite,
enable checks before tool calls and reply release, and supply that policy ID.
Planning makes zero provider calls. Review the exact combined plan hash and
budget before separately approving one run.

`cutback security prepare` also accepts an explicit
`isolated-record-guarded-tools-v1` contract alongside the suite evaluation file.
Its default budget is zero. The worker requires the separate checker
configuration documented in [guarded tools](guarded-tools.md); a pending or
retired approval does not authorize execution.

Administrators and viewers can inspect the metadata report and canonical tool
effects/release receipts. Private rules and case content retain their existing
administrator purpose checks and encrypted retention. Browser credentials stay
in memory.

## Limits

The seven owned attack categories and literal/structural/tool-name rules remain
bounded test mechanisms. These results do not establish semantic jailbreak
resistance, real-model robustness, a general false-positive rate, production
tool support, customer savings or complete task cost. Adaptive red teaming,
external benchmark adapters and broader targets remain separate engineering
work.
