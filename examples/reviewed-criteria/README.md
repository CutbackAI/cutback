# Independent reviewed-criteria sample

This sample classifies synthetic support tickets into category and priority. It has no connection to a private application and requires no provider key. The generator proposes a field comparison using training rows; a demonstration-only review pins it before validation and untouched-test confirmation.

`app.py` is the independent native SDK classifier. `check_application.py` exercises it with an owned HTTP transport. After installing the source environment, run `uv run python examples/reviewed-criteria/check_application.py` to verify the application itself, then `uv run cutback scan examples/reviewed-criteria` to inspect it without importing or executing it. The full demo also registers the exact source request, completes confirmation and prepares a model diff with rollback; it does not apply that diff or deploy it.

From a matching source checkout:

```sh
uv sync --locked --all-extras --dev
uv run cutback demo --reviewed-criteria
```

Docker and Git must be available. The demo creates its own local PostgreSQL stack and encrypted workspace, launches the real collector/dashboard, and intercepts every provider request in an owned synthetic transport. It never uses a production database or a paid inference endpoint. Follow the local URL printed by the command; keep its access link private. Stop the command when finished so its owned stack can shut down.

In **Experiments → Reviewed evaluation criteria**, inspect the completed version. Read its private content with a stated purpose, inspect the unchanged thresholds, and compare the validation/confirmation runs. Generation accounting appears separately from target evaluation spend. The synthetic approval and synthetic quality result cannot substantiate a customer claim.

To prepare a new version manually, open **Propose checks from training examples**. Freezing the plan makes zero provider calls. Separate generation-budget approval, queuing, private review and target budgets are required. A live worker with real credentials may bill approved requests; obtain the owner's approval before using that path.

The native fixture and durable journey are implemented in `cutback.demo_criteria`; real HTTP/PostgreSQL/native SDK acceptance is in `tests/end_to_end/test_reviewed_criteria_pipeline.py`, including authenticated admin/viewer, mobile and accessibility checks. [Detailed workflow, CLI and evidence limits](../../docs/reviewed-criteria.md).
