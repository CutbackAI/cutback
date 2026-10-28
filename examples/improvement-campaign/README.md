# Source-bound improvement campaign

Status: locally accepted source; see the progress record for hosted/publication gates. PyPI dev5 does not contain it yet. No real model performance or customer savings is claimed.

From this source checkout, run `uv run cutback demo` with Docker Desktop running. Choose **Explore sample**, then **Follow an automatic improvement campaign**. The demo uses PostgreSQL, encrypted content, actual pinned OpenAI SDK requests with owned HTTP transports, scoped HTTP APIs and the same durable execution ledger used by the operator worker. It requires no provider key and makes zero vendor requests.

For an already running **owned local** collector with explicitly authorized encrypted storage, run:

```powershell
uv run python examples/improvement-campaign/sample.py --local-target .
```

Add `--outcomes` for ordinary agent preparation → native search → application checks → encrypted diff → later task outcomes. The later synthetic fixture is deliberately cheap but worse and must return `REVIEW_ROLLBACK`. Read [the outcome walkthrough](../../docs/campaign-outcomes.md) for trust, accounting and cleanup boundaries. No live application is deployed.

The collector and worker code must both come from this source checkout and have migration `0015`. The script reads the owned local configuration privately, creates a synthetic project, leaves its receipts available in the dashboard and writes the independent classifier only under the target's ignored `.cutback/campaign-example/` directory. Add `--cleanup` to explicitly delete that project afterward. It never reads another application's source or examples.

Open [the classifier](../../src/cutback/sample_app/campaign.py): it uses the native SDK and has no Cutback import. The sample scans a copied file without executing it. It registers that exact source profile, imports forty synthetic examples, and freezes an explicitly declared synthetic business-cost window. Its conservative opportunity forecast bounds the complete search before approval.

The owned provider fixture deliberately produces these results:

| Arm | Change | Expected result |
| --- | --- | --- |
| Reference | Original model and instruction | Label A |
| Simple downgrade | Cheaper model, original instruction | Wrong label B; rejected |
| Prompt-only alternative | Original model, generated shorter instruction/output cap | Label A |
| Combined alternative | Cheaper model, generated shorter instruction/output cap | Label A; selected by fixture modeled cost |

One generation operation creates two encrypted, frozen candidate versions. A separately hashed ordinary experiment evaluates the reference and all three alternatives on validation groups. The worker freezes one winner and confirms it on untouched test groups. Every physical request has a pre-call child **and parent** reservation; generation is included in the total. The final decision explicitly says that real-data validation is still required. Token counts and answers are fixture values, not measurements of the named models.

Inspect **Campaigns** for stage identities, version hashes, known/unknown cost coverage and approval bounds. Inspect **Task economics** for the synthetic baseline declaration. Raw instructions stay encrypted and purpose-scoped. The fixture also prepares an exact literal instruction/model/output-cap diff and rollback without changing its application. The ordinary agent path connects that proposal to portable checks and an optional local review branch; see [the complete workflow](../../docs/improvement-campaigns.md). Hosted/publication gates are recorded in [progress](../../docs/progress.md). No application is deployed.
