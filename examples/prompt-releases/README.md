# Independent prompt release example

This source-checkout example creates its own synthetic prompt and workflow, stores two encrypted versions, grants a short-lived reader, renders locally, promotes an alias twice and restores its exact previous version. It makes no inference calls. Nothing from another application is used.

Use a disposable local Cutback project with PostgreSQL migrations applied, encrypted payload storage enabled and an authorized project-admin credential. Install the current checkout with `python -m pip install -e ".[server,prompts]"`. PyPI dev5 does not include the prompt registry.

Set `CUTBACK_ENDPOINT` to the loopback collector URL, `CUTBACK_PROJECT_ID` to that project's UUID and `CUTBACK_CONTROL_TOKEN` in your process environment. Never put tokens in committed files or command arguments. Run:

```sh
python examples/prompt-releases/sample.py
```

Expected metadata: two versions, alias revision three, `provider_calls: 0`, and unverified synthetic quality. Open **Prompts** in the dashboard and select the returned key. Inspect the version metadata and alias history; content requires a purpose. The temporary reader key is revoked at the end; synthetic records remain until the disposable project is deleted.

For an experiment, import independently authorized cases for that workflow, open **Compare in a budgeted experiment**, choose the two versions and a validation split, then prepare a frozen plan. It requires a separate exact-hash budget approval before queueing. Preparing a plan never runs a provider. Structural validity or agreement alone does not establish task quality. See [contracts, current-policy reads and limitations](../../docs/prompt-releases.md).

The execution worker needs the selected provider extra: `[openai]` for OpenAI or local Chat, `[anthropic]` for Anthropic. Install it before enabling an explicitly approved worker; the no-inference example itself does not require either provider SDK.
