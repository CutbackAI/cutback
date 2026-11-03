# Measure SDK instrumentation overhead

From a source checkout, reproduce the ordinary synchronous SDK hook profile without
provider credentials, paid requests, a collector service or PostgreSQL:

```sh
uv sync --locked --all-extras --dev
uv run --locked python scripts/instrumentation_profile.py \
  --profile examples/load-qualification/native-hook-profile.json \
  --receipt .cutback/qualification/native-hook.json
```

Run the alternative pinned OpenAI SDK envelope separately:

```sh
uv sync --project infra/sdk-compat/openai3 --locked --python 3.11.18
uv run --project infra/sdk-compat/openai3 --locked python scripts/instrumentation_profile.py \
  --profile examples/load-qualification/native-hook-profile.json \
  --receipt .cutback/qualification/native-hook-sdk3.json
```

Use a new receipt path for each execution. The tool refuses to replace an existing
receipt, creates private output directories, removes inherited Cutback/provider
credentials inside its own process, and permits only the repository's qualified
SDK versions. It never installs packages or changes your application environment.

The declared profile measures OpenAI Responses, OpenAI Chat Completions and
Anthropic Messages. Each surface runs with a successful collector transport and
with a collector connection outage. Each condition uses four trials in plain,
observed, observed, plain order; each trial has ten excluded warmup calls and 200
measured calls. Responses come from closed native SDK transports. The production
instrumentation hooks and bounded background exporter run normally, with exporter
HTTP replaced by a closed transport. There is no network request to a provider or
collector. Forced flush and shutdown occur after the measured calls.

The receipt retains raw call durations and signed, per-index observed-minus-plain
differences. Its median target is at most five milliseconds of added latency for
every declared surface/collector condition. Negative differences remain as timing
noise; they do not establish that instrumentation makes inference faster. The
receipt also records the nearest-rank 95th percentile, SDK/runtime identity,
export/drop accounting, queue drain, and metadata privacy checks. Partial trials,
native response changes, failed exporter accounting or unexercised outages fail
the profile even when surviving timings are fast.

This is a synthetic hook benchmark. It does not measure real provider latency,
serverless cold starts, async calls, streaming, process memory, sustained exporter
network throughput, application task cost or customer service levels. Run it on
your target hardware and record competing load when interpreting results. The
functional SDK matrix separately covers native responses, async and streams,
retry behavior, outages and privacy; passing those tests does not imply measured
performance for those paths.

The `mixed-services` CI matrix runs this profile and its failure/privacy tests for
both pinned OpenAI envelopes, then retains the receipt with the simultaneous
service-load evidence. A failed or incomplete result remains a failed gate.

On 1 November 2026 both local pinned envelopes passed the declared 200-sample
profile. The largest signed median difference was 0.4426 milliseconds. Both ran
concurrently on the same Windows host with other qualification workloads; the
[raw acceptance evidence](evidence/native-hook-acceptance.json) preserves that
limitation, every duration, exporter accounting and actual runtime identity.
Exact-head hosted acceptance remains a separate release gate.
