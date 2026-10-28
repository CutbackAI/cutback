# Native application budgets, without a vendor key

Source checkout only; PyPI dev5 does not contain this addition. The [contract and limits](../../docs/gateway-budgets.md) and [progress](../../docs/progress.md) distinguish local qualification from final publication.

Install the matching source in a fresh environment and start Docker Desktop:

```powershell
uv sync --locked --extra gateway --extra openai
uv run cutback collector up --target . --port 8080
uv run cutback collector status --target .
uv run python examples/gateway/sample.py --local-target .
```

Use a clean checkout or a reviewed source-matched owned local collector whose readiness reports schema `0019`. This example accepts only an owned numeric-loopback/local collector. It needs neither a provider key nor payload capture permission. It does not inspect another application's source or data, start a production gateway or read inference credentials from the environment.

Open [the independent native SDK application](../../src/cutback/sample_app/gateway.py). Its SDK call remains ordinary; `operation_headers` binds one logical UUID across retries. The sample starts a real loopback ASGI gateway with an exclusively synthetic provider transport, then uses actual native SDK HTTP and real PostgreSQL.

Expected sequence:

1. Prepare and approve a finite project/team/model policy, then issue two one-time environment-scoped keys.
2. A native text response records one known modeled fixture cost.
3. Reusing that operation UUID returns a nonretryable conflict and makes no second provider request.
4. A second fixture omits usage. Its full reservation remains held; complete modeled cost is unknown.
5. A newly issued key cannot evade the shared team/model allowance. The next request stops before the provider transport.
6. Stop the owned gateway, revoke both sample keys and disable the policy. Preserve metadata for dashboard inspection.

The summary must show **two synthetic provider requests**, `duplicate_blocked: true`, `rotated_key_shared_limit_blocked: true`, one `SETTLED` receipt, one `UNCERTAIN` receipt and **zero paid provider calls**. Answers, counts and modeled costs are owned fixtures; they do not establish real task quality, vendor billing or customer savings.

Open the returned dashboard URL and choose **Application budgets**. Review scopes, exact price/hash references, current per-call holds, shared counters and unknown-cost receipts. The sample retains no usable application key or enabled gateway after completion. Collector credentials stay in the owned local workspace; never paste them into issues or public reports.

Each run saves a metadata-only journal below `.cutback/gateway-example/`. A failed run remains visibly incomplete. If a project-create response is lost, the exact generated slug remains for explicit operator reconciliation; the runner never deletes a merely similar project.

Add `--cleanup` to explicitly delete this sample project after acceptance. A cleanup failure is reported as unconfirmed. To stop the local development collector while preserving its database, use `uv run cutback collector down --target .`.

The first gateway contract is synchronous/asynchronous nonstreaming Luna Responses text. Other providers, Chat/tools/modalities/streaming, native fallback policies and production operational acceptance remain unqualified. Follow the frozen request/purpose/price contract rather than treating this example as a universal reverse proxy.
