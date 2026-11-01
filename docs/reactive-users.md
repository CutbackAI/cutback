# Reactive user simulation

Execution receipts distinguish the requested model from the model identifier
returned by the native SDK. Missing, unsafe or historical identifiers remain
unknown; configuration cannot reconstruct them. Response identity is metadata,
not invoice verification. The receipt retains the approved price reference and
known modeled cost separately.

This bounded source implementation is accepted in [PR 58](https://github.com/CutbackAI/cutback/pull/58).
Exact artifact/hosted gates and limits are in [progress](progress.md). It is not
included in published PyPI dev5; a source merge does not publish a package.

Reactive simulation runs a separately approved user model after the original
scripted user turns. The model receives the persona and the conversation actually
observed so far, including returned tool results. It can propose one user-role
follow-up or end the conversation. It cannot change tools, permissions, task
assertions, hidden state, the original question, or target turn limits. Gold,
reference answers, system prompts, unreturned fixtures and assertions are excluded
from its input projection. Input byte limits and closed output validation apply
before any proposed user message reaches the target.

The initial profile supports native fixture episodes and an OpenAI Responses text
user model from the existing pinned support matrix. It does not combine reactive
users with semantic judges, isolated record tools, security suites, source patch
plans or staged campaigns. Those combinations require their own qualification.
The original scripted prefix must still match exactly. Additional user turns must
match the approved reactive bound and the recorded generated-turn count.

## Try it

From a source checkout, run `uv sync --locked --all-extras --dev`, then
`uv run python examples/reactive-user/app.py` for the offline native SDK example, or
`uv run cutback demo --reactive-users` for PostgreSQL and the dashboard. Both use explicitly
synthetic transports. The test double reacts to the observed answer to demonstrate
message flow; it provides no evidence of a real model's intelligence or savings.

In **Experiments → Plan an experiment**, select a typed fixture episode dataset
and enable **Add a reactive simulated user**. Select its model, persona, transcript
bound and one to three maximum user turns. Freeze the proposed plan, inspect its
complete forecast and approve its exact hash before queueing. An operator must
explicitly enable native execution and configure the approved provider key.
Content capture authorization and vendor rights remain required, and are checked
again before each target and simulator call.

API clients can supply `user_simulation` to `POST .../experiments/draft`, containing
`model`, `persona`, `max_turns`, and `max_transcript_bytes`. The server freezes the
owned instruction and JSON response contract into `deterministic-v17` and
`native-fixture-reactive-user-v1`. Direct plan clients must use that same canonical
contract; changing it requires a new supported execution version.

## Budgets, evidence and failures

The forecast reserves up to the original target turn bound plus the simulator call
bound for every target/case/repeat. Atomic durable reservations precede every call.
Simulator calls have purpose `simulation`, their own approved model identity and
per-episode call cap. They also consume the shared run and project quotas. A
simulator cannot spend a target turn slot or run outside its target episode.

A finite modeled cost above the reserved request bound stops the evaluation and
blocks later dispatch through the shared ledger. The reported usage and known
modeled subtotal remain recorded. These amounts use the pinned rate catalog;
invoice reconciliation is a separate evidence source.

Reports separate target requests and target cost from the simulator subtotal while
also showing total evaluation spend. Unknown usage stays unknown. Malformed,
refused, truncated, oversized or unavailable simulator responses cannot produce a
passing conversation. Uncertain billable operations are never automatically
replayed; unknown simulation billing stops subsequent evaluation calls. Reaching
a target turn limit cannot silently increase the original scenario's authority.

Generated user messages exist only inside explicitly retained encrypted case
evidence. Metadata reports contain bounded counts, stopping status and costs.
Reading conversations requires the existing administrator permission and a purpose;
viewer access is denied. Existing privacy deletion and retention cover the same
case artifacts and call ledger.

These are model-simulated users, not representative human behavior. Identical
persona policies can produce different trajectories across candidates. Do not
describe those trajectories as identical pairs, independent human samples,
production task success, or complete task economics. Tool and hosting costs are
not inferred from model receipts.
