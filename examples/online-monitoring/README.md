# Independent online monitoring sample

This source-checkout example creates one synthetic technical failure and a versioned, metadata-only rule. It does not need a model API key. It does not configure external notifications or use a private application.

1. Start your local PostgreSQL-backed collector and migrate it with this checkout. Create an isolated synthetic project plus an admin control key and staging ingest key. See [local setup](../../docs/quickstart.md).
2. Set `CUTBACK_ENDPOINT`, `CUTBACK_PROJECT_ID`, `CUTBACK_CONTROL_KEY` and `CUTBACK_INGEST_KEY` in the local process environment. Do not put keys in source, shell transcripts or shared URLs.
3. Run `python examples/online-monitoring/sample.py`. The script approves a rule bounded to its unique synthetic workflow, ten evaluations and zero provider dollars.
4. Start the self-host worker with `CUTBACK_ONLINE_EXECUTION=true`. Leave inference and webhook execution disabled. Open **Monitoring** in the authenticated dashboard and refresh. A closed sampled window produces an incident. Investigate its retained trace, acknowledge it, assign an owner reference and resolve it.
5. Disable the synthetic rule after inspecting it, or delete the isolated project through Data controls. Existing customer rules are not changed.

The metadata-only rule is not a task-quality judge. Capture, human review and new dataset promotion still require their own current rights, content and independent judgments. The executable acceptance test drives these same API calls against disposable PostgreSQL; it does not substitute a hard-coded incident response.
