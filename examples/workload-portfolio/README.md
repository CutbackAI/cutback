# Watch the agent investigate repeated work

Available in the current GitHub source, accepted in
[PR 74](https://github.com/CutbackAI/cutback/pull/74). The published PyPI dev5 package
does not include this command. Use the matching source checkout; Docker and Git
must be available. No provider account, API key or private application is needed.

```sh
uv sync --locked --no-dev --extra server --extra openai
uv run --no-sync cutback demo --workload-portfolio
```

The command starts its own temporary local PostgreSQL database and collector,
then opens the dashboard. In **Sample**, find **Can repeated work avoid another
provider call?** and choose **Inspect the repeat-workload confirmation**.

This walkthrough needs the server and OpenAI extras, Git and Docker; it does not
need the development, identity, billing, cloud-storage or other provider packages.
A fresh minimal installation completed the actual CLI walkthrough, confirmed the
source change and rollback, and removed its owned database/listener after Ctrl+C.
All provider transports remain closed synthetic fixtures.

The independent application is a synthetic billing classifier. The complete
walkthrough performs these steps:

1. Scan a clean committed application without importing it into the scanner.
2. Observe eight actual SDK calls through a closed synthetic HTTP transport.
3. Analyze the authorized encrypted profiles and find three potential reuses.
4. Prepare model, output-cap and workload-qualified cache hypotheses.
5. Run the explicitly synthetic validation trial and select the eligible winner.
6. Confirm that candidate using separate untouched fixture groups and approval.
7. Prepare a source patch, check both application trees, and verify its rollback.

The dashboard shows the canonical confirmation, modeled trial costs and the
proposed independent source change. Source fixtures and the private journal stay
under `.cutback/support-desk-demo` in the directory where you ran the command.
Runtime caching stays disabled; the original application source is unchanged.
Stop with **Ctrl+C** to remove the owned temporary database and stop its server.
The local source workspace remains for inspection; its deleted demo project
cannot resume after shutdown.

Every provider response and token count is synthetic. Both trial plans carry the
explicit native-fixture contract, which a live-provider worker must refuse.
The normal real-quality gate fails; only the fixture's sample gate can pass.
This establishes the software pipeline, not customer quality, invoice charges,
production hit rates or realized savings. Full task and deployment costs stay
unknown. [Contracts and acceptance](../../docs/workload-native-portfolios.md).

For an existing owned collector, the separate
[request-reuse example](../request-reuse/README.md) can retain an independent
application and prepare a pending trial without granting any inference approval.
