# Modern text usage and effective-dated prices

This source addition observes the qualified text usage fields of GPT-6 Luna. It does not enable Luna as a paid experiment target. Existing native experiment contracts and their primary price-snapshot hash stay unchanged. PyPI `0.1.0.dev5` does not contain this addition.

## Try the arithmetic without a provider key

From an installed source checkout:

```sh
python examples/modern-pricing/sample.py
```

The independent synthetic event has 12,000 input tokens: 4,000 cached reads, 5,000 thirty-minute writes, 2,000 twelve-hour writes and 1,000 ordinary input tokens. Its 100 output tokens are a separate bucket. The modeled total is **0.001315 USD**. Removing a write-lifetime count produces an unknown total. The script performs no network requests and uses neither an API key nor a database.

The real native Node → collector → PostgreSQL → report fixture also exercises this contract:

```sh
python scripts/check.py -q tests/end_to_end/test_node_pipeline.py
```

It needs the owned local PostgreSQL test service and the pinned Node dependencies; provider HTTP is synthetic. `--modern-cache` on the Node sample selects the new fixture. The corresponding OpenAI event costs 0.00000507 USD; the two-provider report totals 0.00005007 USD. These are arithmetic expectations, not model benchmarks or invoice evidence.

## Count disjoint token buckets

The checked [official model contract](https://developers.openai.com/api/docs/models/gpt-6-luna) and [pricing documentation](https://developers.openai.com/api/docs/pricing) specify inclusive input usage and separate cache-write durations. A write is not also ordinary input. For Standard text requests the checked rates per million tokens are 0.10 input, 0.01 cache read, 0.125 thirty-minute write, 0.25 twelve-hour write and 0.50 output. Above 272,000 input tokens, the entire request's input/cache buckets use a ×2 multiplier and output uses ×1.5. The threshold is exclusive.

The secondary immutable snapshot `openai-text-2026-10-28-v1` records its checked date, sources, exact model ID and limitations. It is effective from the checked date; no historical price applicability is inferred. Regional processing, Fast/Batch/Flex tiers, tools, image/audio/video and other special units remain unpriced. This is a conservative observed-usage cost model, not invoice reconciliation. Explicit operator catalogs disable the bundled secondary fallback.

Missing total writes, missing nonzero-write durations, inconsistent partitions or unsupported units leave the full amount unknown. A known zero total write count needs no guessed duration. PostgreSQL amounts outside its exact 12-decimal-place USD representation also remain unpriced, with a reason and the calculated bucket evidence; ingestion must neither fail nor silently turn a tiny nonzero cost into zero.

## Upgrade collectors before producers

Events containing the new thirty-minute/twelve-hour fields use event schema `1.1`. The ingestion batch envelope remains `1.0`. Upgrade the collector before the observer: older strict collectors reject these new events. There is no protocol negotiation or automatic retry of dropped SDK exports. Native event `1.0` serialization omits both new fields entirely, preserving released fingerprints and prior approval evidence.

Python qualification uses OpenAI 2.29.0 and 3.35.1 with their actual native sync/async, Responses/Chat and streaming types. Returned lifetime metadata is projected conservatively, including supported native model extras. Responses observation recognizes only the exact Luna `extra_body={"prompt_cache_options": {"ttl": "30m"}}` or `"12h"` option; broader extra-body contracts remain unsupported. The original request, response, retry and stream semantics remain owned by the provider SDK. This does not add that option to the paid experiment executor's support matrix. Native Node qualification uses its separately pinned SDKs.

The returned service tier takes precedence over the requested tier. If the response omits it, the requested tier is retained. Unsupported actual tiers cannot inherit Standard prices. Anthropic five-minute and one-hour write counts are disjoint; an aggregate cache-creation count is not billed again as the five-minute bucket. Ambiguous duration usage remains unknown. Existing recorded valuations are never automatically recalculated.

## Enter a private contract quote

Open **Security → Public list-price snapshots** to see readable rates, cache durations, context premiums, checked dates and source links. Effective-dated project rates appear below with cursor pagination.

An administrator can expand **Add effective-dated contract rate**, select the exact provider/model/region/tier and enter rates. Monetary fields start blank. For this OpenAI contract select the versioned inclusive-write option and supply both lifetime prices, the context threshold and both multipliers. Null region/tier is an exact unspecified scope, not a wildcard; use `default` when the application reports that actual tier. Rates are USD per million tokens.

Quotes are immutable. A later start supersedes an earlier matching quote for later events; already recorded events keep their valuation. Repeating the identical scope/start/rates/end returns the same ID. A different quote with the same scope and start conflicts. Legacy ambiguous active quotes with the same start leave cost unknown instead of selecting an arbitrary rate. A project retains at most 10,000 quotes; an identical replay still works at that limit. Listing uses a project-scoped UUID cursor, at most 100 rows per request. Export and project deletion include contract quotes and extended event metadata under the existing privacy boundary.

## Acceptance scope

Actual native transports, independent Decimal arithmetic, old event/catalog hashes, unknown usage, real PostgreSQL ingest/idempotency/RBAC/export, concurrent quote replay, quotas, pagination and authenticated desktop/mobile/accessibility checks are in the test suite. Final package and public-check qualification is recorded in [progress](progress.md). No paid provider request, automatic price refresh, model-quality claim or deployment is part of this example.
