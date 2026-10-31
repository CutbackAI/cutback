# Compare the model and retrieval together

Source-checkout feature under qualification. Published `0.1.0.dev5` does not
contain this workflow. Use a matching source collector and CLI; inspect the
actual [acceptance record](progress.md) before adopting a release.

Run `cutback demo --joint-model-retrieval` for an independent committed support
application, local PostgreSQL and real native SDK parsing with intercepted
synthetic HTTP replies. Git and Docker are required. The sample executes
validation, separately approved untouched confirmation, checks against both
source trees, a local review reference and exact rollback. It uses no provider
key and makes no paid request. Its answer quality and usage are synthetic.

The joint plan compares at most five alternatives on the same independently
labeled questions. Alternatives vary qualified native model choice, output
ceiling and actual lexical retrieval settings, including combined alternatives.
The corpus, query framing, instruction and input bound stay frozen. There are no
hidden prompt generator calls. Output caps are hypotheses: lowering a cap alone
does not establish lower actual spend, and incomplete or worse answers fail the
quality gate. Every target call uses the existing pre-call reservation ledger.

For the independent [example](../examples/retrieval-optimization/README.md),
commit the copied application outside Cutback, then run:

```sh
cutback optimize . --json
cutback workflow prepare --local --automatic \
  --example questions.jsonl --retrieval-corpus-authorized \
  --rights-basis synthetic --rights-reference owned-support-corpus \
  --independent-labels --minimum 1 --min-test-samples 3 \
  --assumed-usd-per-cpu-hour 0.01 --max-usd 5 --max-requests 500
```

Automatic preparation recognizes only the exact source-qualified corpus
consumer. It parses source and numeric settings without importing the
application. Corpus upload needs the separate explicit authorization above and
an enabled encrypted capture policy. `--joint-retrieval` selects this engine
explicitly; `--retrieval` preserves the original fixed-model v6 workflow. A
stored v6 session retains its original engine when resumed.

Review `workflow status`, its candidate count, complete request bound and exact
hash. When separately authorized to run inference, queue that hash with
`workflow continue --plan-hash HASH --approval-reference APPROVAL`. Poll using
`workflow continue` without changing identity. The ordinary worker can spend
real money; the guided demo supplies its own intercepted synthetic transport.

Read each candidate's answer checks, changed axes, native API subtotal and
declared CPU component costs. Unknown CPU or native usage prevents automatic
economic selection. Each variant includes its own measured index construction
and query CPU; the declared CPU price remains an assumption. Native latency
excludes retrieval preparation. Memory, idle infrastructure, tools, full task
cost and realized customer savings remain unknown.

```sh
cutback workflow confirm --candidate CANDIDATE_KEY --max-usd 5 \
  --max-requests 100 --purpose-reference owned-confirmation
```

Confirmation freezes exactly one eligible candidate and untouched groups.
Review and approve its new hash separately before execution. An insufficient
confirmation budget fails before saving an intent, so it can be corrected.
After a lost confirmation reply, reuse the exact candidate and budget to
reconcile the same request. Uncertain billable operations cannot be retried
automatically. Revoked rights, expired policy or exposed holdouts fail closed.

After confirmation, inspect `workflow gate --sample-only` for the synthetic
sample, then prepare the private source artifact:

```sh
cutback workflow proposal --purpose-reference owned-review --output ../joint-change.json
cutback model-retrieval patch . ../joint-change.json --output ../review-change.json
```

The artifact changes only required native literals and numeric retrieval
settings. It includes a forward patch, exact reverse patch and hashes of the
changed files. No patch is applied by these commands. Use the example's portable
`checks discover` and `checks run` against separate committed base/proposed
trees, then use `cutback model-retrieval review --help` to verify their exact
receipts and declared consumer coverage. A local review reference cannot
overwrite another commit. Local checks are owner-executed evidence, not an
independent CI attestation.

The dashboard's Retrieval page offers **Compare model choice and output limits
too**. For a source-bound proposal, upload a receipt from:

```sh
cutback model-retrieval inspect-producer APP_ROOT app.py --output ../inspection.json
```

Bound baseline fields remain fixed. The page routes each historical run by its
stored contract, permits explicit eligible-candidate selection and confirmation
budget review, and downloads the matching proposal type. Preparing a plan never
approves or queues it. Credentials and uploaded content remain in memory.

The new `deterministic-v18` / `cutback.joint-model-retrieval.v1` contract keeps
legacy v6 serialization unchanged. Old retrieval endpoints reject joint plans
rather than silently dropping their native model change. Collector readiness
must advertise the joint contract and the exact local retrieval recipe before
automatic setup uploads any object.

This is a joint native/retrieval search. It does not complete cross-engine
selection across caches, learned routing, compression and training. Remote or
vector retrieval, arbitrary dynamic source adapters and production deployment
remain separate requirements.
