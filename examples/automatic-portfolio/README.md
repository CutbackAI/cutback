# Let the agent compare alternatives

An independent classifier answers an owned synthetic task. Cutback's agent prepares several model/output-token configurations, compares their answers to independent fixture labels, and confirms one passing lower-cost candidate before proposing an exact source change. The sample has no connection to a private application.

Start from a source checkout with Python 3.11–3.13, Git and running Docker Desktop:

```sh
uv sync --locked --all-extras --dev
uv run cutback collector up --target . --allow-payloads
uv run python examples/improvement-campaign/sample.py --local-target . --outcomes
```

The ordinary source-bound agent path uses the same `workflow prepare --for-campaign`, `campaign prepare`, finite operator and application-check adapters available to an application owner. New campaign preparation selects v3 and freezes up to four static alternatives. Native provider HTTP is intercepted by the sample's explicit synthetic transports; the command needs no provider API key and makes no paid request. Its answers, usage and prices are fixture evidence, not measurements of the named real models.

Read the terminal's collector/dashboard URL and open **Campaigns**. The first command starts an owned loopback collector with encrypted sample storage in the ignored `.cutback/` workspace. The script leaves its synthetic project available for inspection; `--cleanup` deletes only that project. Stop the owned collector with `uv run cutback collector down --target .` when finished. Inspect the complete campaign before studying the rest of the repository:

1. **Frozen economic bounds:** model/output-cap alternatives, cumulative native dollar/request ceiling, reserved instruction-search effort and complete baseline cost reference. Preparation itself executes no inference. If the full portfolio does not fit, inspect which alternatives were omitted before approval; validation and confirmation samples stay intact.
2. **Initial validation:** all frozen static alternatives use the same held-out validation cases and unchanged reference. A cheap wrong answer cannot qualify. The sample deliberately makes the simpler alternative fail so the reserved instruction-search path is visible.
3. **Instruction search:** only if static validation found no eligible winner and the frozen budget already reserves this stage. Candidate instructions are encrypted. The proposer receives permitted training examples and aggregate validation feedback; it never receives validation/test examples.
4. **Confirmation:** freeze one eligible candidate and compare it on untouched test families. Failure ends the campaign. Reimporting the same cases cannot reset their exposure history.
5. **Application review:** inspect the exact diff, rollback and checks on both clean source trees. The sample leaves the application unchanged and labels its decision synthetic. A source binding does not prove independent test coverage or production savings.
6. **Task economics:** the optional synthetic later-outcome walkthrough retains all campaign operation costs and separates declared assumptions from measured windows.

The existing guided `uv run cutback demo` also retains a v2 single-alternative campaign for compatibility. Preparing a **new** campaign in its Campaigns page uses v3 and shows the frozen portfolio table before approval. Saved v1/v2 campaigns resume with the original hash and order.

For an authorized application's own source and data, follow the [complete campaign workflow](../../docs/improvement-campaigns.md). A coding agent can discover source contracts, prepare permitted examples, locate a matching baseline, derive the portfolio, run an already approved campaign, prepare the reversible change and execute authorized checks. Data rights, vendor permission, cost assumptions, paid execution and production release still need actual owner authority; the synthetic sample does not supply it.

[Exact acceptance and open engineering gates](../../docs/progress.md) · [Native support limits](../../docs/providers.md) · [Economics semantics](../../docs/task-economics.md)
