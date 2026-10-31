# Measure repeats before trying a cache

This example sends eight actual OpenAI SDK calls through an in-process synthetic
HTTP transport. Four requests repeat; four are distinct. The expected analysis
has **eight profiled attempts and three modeled potential hits**. All eight
provider calls still execute: observation does not enable a cache.

This is an **unreleased source feature**, requiring the matching collector and
worker at schema `0032`. The published PyPI dev5 package does not include it.
Use the repository's locked source installation and an owned local collector with
encrypted storage enabled; follow [self-hosting](../../docs/operating-and-recovering.md).
The ordinary worker can analyze profiles with inference disabled.

1. Set `CUTBACK_CONTROL_TOKEN` in your process to an administrator key for that
   local collector. Keep credentials out of shell history and source files.
2. Run `python examples/request-reuse/demo.py --endpoint http://127.0.0.1:8080`.
3. Confirm `synthetic_native_calls: 8`, `paid_provider_calls: 0`,
   `modeled_potential_hits: 3` and `cache_trial_eligible: true` in the JSON.
4. Add `--keep-project` to inspect the synthetic project in the dashboard's
   **Request reuse** page. Delete it through project controls when finished.

The script creates a separate synthetic project, capture policy and ingest key.
Its random profiling key exists only in process memory. It deletes its project
by default, including encrypted samples, and restores prior process settings.
Provider credentials are synthetic; the fixed transport cannot contact a vendor.
The collector is restricted to loopback HTTP. A collector and worker must already
be running; the script does not provision infrastructure.

Potential reuse is a model of this retained stream, starting cold and assuming
zero cache transport latency. It is not a production hit rate, quality result,
customer saving or invoice. A separate cache trial must establish delivery,
independent task quality and infrastructure economics before any source change.
See [the complete contract and troubleshooting](../../docs/request-reuse.md).
