# Let the agent prepare a retrieval improvement

Source-checkout extension, under qualification; published dev5 lacks these flags.
Use the matching source collector and isolated environment. The independent
[support application](../examples/retrieval-optimization/README.md) provides
synthetic questions, corpus, settings and a real local application check.

For a guided run with Docker and Git, use `cutback demo --agent-retrieval`.
It creates an independent committed application and runs the actual agent,
collector, PostgreSQL worker and SDK parser using intercepted synthetic replies.
It checks both source trees, creates a local review ref and verifies rollback.
Open Retrieval history to inspect its validation and separate confirmation;
the private sample report contains `agent_retrieval_sample`. No provider key
is used and no inference endpoint can be reached by its synthetic executor.

The scanner recognizes that exact bounded producer with literal native model
and output ceiling. It reads source and numeric configuration without importing
the application or parsing corpus content. Dynamic builders, changed contracts,
untracked files and ambiguous workflows require inspection instead of invented
coverage. This qualification does not cover arbitrary RAG frameworks.

Copy the example directory outside Cutback and commit it in a clean independent
Git repository. Use the existing local collector instructions to enable the
encrypted store deliberately. In that independent repository:

```sh
cutback optimize . --json
cutback workflow prepare --local --automatic \
  --example questions.jsonl --retrieval-corpus-authorized \
  --rights-basis synthetic --rights-reference owned-support-corpus \
  --independent-labels --minimum 1 --min-test-samples 3 \
  --assumed-usd-per-cpu-hour 0.01 --max-usd 5 --max-requests 500
```

Inspect installed help before running these commands. `--retrieval` selects this
engine explicitly instead of `--automatic`; do not combine both. The corpus
authorization is separate from question rights and remains required with either
choice. The illustrative CPU rate is an assumption, not a measured invoice.
Preparation creates an expiring encrypted plan and makes no inference call.
The native model and output ceiling stay unchanged; the bounded search changes
chunk size, overlap, top-k and lexical reranking only.

Inspect `workflow status` and the exact plan. With explicit inference authorization,
`workflow continue --plan-hash HASH --approval-reference APPROVAL` queues that
one plan on an explicitly enabled worker. Subsequent `workflow continue` polls
the same run. A lost approval or queue reply cannot dispatch a replacement run.
A normal worker can spend real provider money: use `cutback demo` to explore
synthetic native transport without enabling ordinary provider execution.

After validation, choose an eligible candidate from the recorded API-plus-declared-
CPU component comparison. Unknown CPU or native usage prevents automatic
economic selection. Request latency excludes retrieval preparation; full task
cost, infrastructure, production hit rate and realized savings remain unknown.

```sh
cutback workflow confirm --candidate CANDIDATE_KEY --max-usd 5 \
  --max-requests 100 --purpose-reference owned-confirmation
```

This freezes one candidate for untouched test groups; it does not approve it.
Approve the new exact hash separately, queue once and poll. Preserve the same
candidate and budget after a lost nonbillable confirmation reply. Changed source,
revoked rights or an exposed holdout cannot be repaired by replacing identities.

After confirmation, use `workflow gate --sample-only` to inspect synthetic
evidence, then `workflow proposal --purpose-reference owned-review --output
../retrieval-change.json`. The proposal leaves the application unchanged and
contains a forward diff and exact rollback. Materialize their files with
`cutback retrieval patch . ../retrieval-change.json --output ../review-change.json`.
It rechecks both the source and exact artifact before writing `.patch` and
`.rollback.patch` beside that output. Follow the example's `checks discover`,
`checks run` and `retrieval review` steps for both committed source trees and
declared consumer coverage. Synthetic evidence remains local review evidence.
No command here pushes an application change or deploys production.

The private metadata journal excludes document passages, questions and credentials.
The corpus remains in purpose-scoped encrypted storage; current policy and
expiry are rechecked before reads and execution. `resume-preparation` reconciles
the same nonbillable scan/policy/dataset/plan identities after an interrupted setup.
It cannot renew rights or retry an uncertain billable operation.
