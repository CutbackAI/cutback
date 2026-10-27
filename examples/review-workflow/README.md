# Import and review an independent synthetic dataset

This example requires an **unreleased source checkout**. PyPI dev5 does not include the import/review workflow. It makes no model calls. Use `uv sync --locked --all-extras --dev`, start the local collector using [quickstart](../../docs/quickstart.md), and open its authenticated dashboard.

1. Under **Security**, create an enabled synthetic rights policy for environment `staging`, workflow `classify`, provider `local`, at least 30 samples and 64,000 bytes per object. Review copies, configuration, each vote, each resolution and each promoted case consume the shared sample quota.
2. Under **Experiments → Import a qualified export**, choose `promptfoo-0.125.1-results.v3` and the checked-in [actual producer result](../../tests/fixtures/imports/promptfoo-0.125.1-v3.json). Select the policy, use workflow `classify`, and authorize the encrypted upload. Review the frozen mapping and optional purpose-audited examples, then confirm the exact mapping and rights. The resulting dataset is **unreviewed**: the two literal source expectations are not independently verified gold.
3. Issue two separate analyst credentials under **Security**. Deliver each token privately to its assigned reviewer. Record their actor/key IDs, not their tokens. A single person using two tokens is useful for exercising this synthetic example but does not establish independent human labels.
4. Under **Review → Create a frozen review queue**, choose the imported dataset and select both examples. Set a concrete classification rubric, check the two assigned analyst credentials by their displayed fingerprints, and choose a separate adjudicator (the local bootstrap administrator can adjudicate). The directory also lists current eligible OIDC members. Freeze the queue under the synthetic policy. These groups are now development evidence and cannot be sold as untouched confirmation data.
5. In separate browser sessions, connect with the two analyst credentials. Open the queue, claim the next item, state the read purpose, inspect the input and historical reference, and submit a JSON string label with rationale. The server withholds source gold and peer answers. Submit different labels for one case to exercise disagreement. Leases last 15 minutes by default; released/expired assignments can be claimed again, submitted votes cannot be overwritten.
6. As the designated adjudicator, open the disagreement after the required blind votes exist, declare a purpose, inspect the two anonymous answers, and record a final label or exclusion. All original votes remain intact.
7. As project administrator, promote the resolved queue to a **new** frozen dataset. Leave the independent-human attestation unchecked when one person exercised both synthetic reviewer accounts. The old dataset, plans, runs and hashes remain unchanged. Excluded cases are omitted; an entirely excluded queue cannot create an empty dataset. Every promoted case links to its source item, queue, votes and resolution.

Use **calibration** mode only with an authorized source dataset containing known gold. Gold remains hidden during review. An administrator can inspect exact-match counts after every item is resolved. This is a measured fixture result, not generalization or reviewer competence on real customers. Calibration queues cannot be promoted.

The API also supports selecting captured trace content (`kind: trace_content` and the content-link UUID returned by trace detail). Metadata-only spans cannot supply missing input/output. All source and destination rights policies must remain current throughout review; revocation/expiry blocks reads, submissions and promotion.

For the exact API, retention and lineage rules see [imports and review](../../docs/imports-and-review.md). Automated acceptance covers separate authenticated browser sessions with real PostgreSQL, blind disagreement, adjudication, promotion, mobile and axe accessibility; see `tests/integration/test_reviews.py` and `web/tests/reviews.spec.ts`.

## Reproduce the qualified upstream fixture

Promptfoo is a **fixture producer only**, not a Cutback runtime dependency. In a separate temporary directory, initialize npm and install `promptfoo@0.125.1` with `--ignore-scripts`. Use a Node version satisfying that package's declared engine. Review/installing an external tool is separate from importing a user's export: Cutback's importer never executes it.

```sh
python examples/review-workflow/generate_promptfoo_fixture.py --producer-root /path/to/isolated/producer --output artifacts/reproduced-promptfoo.json
cutback datasets preview-import artifacts/reproduced-promptfoo.json --profile promptfoo-0.125.1-results.v3
```

The generator checks the exact installed package version and runs that actual upstream CLI with the owned [synthetic provider](synthetic-provider.cjs) and [two-ticket configuration](promptfoo-fixture.json). It gives the process a minimal environment, isolated home/config/cache, disables update checks and telemetry, and inherits no provider credentials. It captures diagnostic output without printing paths. Only the upstream `results` envelope is exported; root configuration/base paths/environment metadata/share URLs are deliberately excluded. Timestamp, latency and run IDs vary. No paid provider or judge is configured.

The other qualified profile uses the actual pinned OpenTelemetry/OpenInference [producer fixture](../workflow-tracing/README.md). Qualification is limited to the documented projection, not all Phoenix exports or all Promptfoo versions/formats.
