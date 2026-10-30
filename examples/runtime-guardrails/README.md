# Check before dispatch and delivery

This independent source example uses its own synthetic support task. It requires no private application, provider account, database or Docker.

From the repository root:

```console
python -m pip install -e ".[guardrails]"
cutback guardrails demo
```

Alternatively run `python examples/runtime-guardrails/main.py`. Both commands launch the actual credential-isolated checker process, provision its private rules through an anonymous pipe and close it afterward.

Read `results` in the JSON receipt:

- `benign`: an owned answer is delivered with complete declared-check coverage.
- `input_deny`: the application function is never called.
- `output_deny`: the application ran, but its result is withheld. Its cost stays unknown; blocking delivery cannot undo a charge or tool effect.
- `unapproved_tool`: an undeclared tool name never dispatches.

The example makes two synthetic application calls and zero vendor requests. It prints metadata only. Literal denies are not semantic prompt-injection detection, and this receipt does not establish real application safety or quality.

For a real integration, review the complete bounded JSON application in `src/cutback/sample_app/runtime_guardrails.py` and [policy, collector, gateway and deployment contracts](../../docs/runtime-guardrails.md). Persistent collector authority needs current content rights, encrypted storage, an exact approved policy and a separately configured scoped checker. The sample grants none of these production privileges.

The source follow-up is under qualification and is not in the published dev5 wheel. Broad installed, recovery and public-head gates must pass before treating it as accepted source.

To inspect the full native SDK → separate checker → gateway → PostgreSQL → dashboard journey, use the source server/OpenAI installation and Docker Desktop, then run `cutback demo --runtime-guardrails`. Choose Runtime guardrails in the dashboard. The sample retains nine check receipts and four native synthetic usage receipts, including withheld output with a known charge. It closes its checker and disables its scoped traffic authority after seeding; no real vendor call or production deployment occurs.
