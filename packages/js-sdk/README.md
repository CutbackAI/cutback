# Cutback Node observer

Apache-2.0 metadata observation for Node 24, OpenAI **7.43.0** Responses / Chat Completions and Anthropic **1.3.0** Messages. These exact SDK versions are tested with synthetic transports. Other versions and custom provider hosts are passed through without instrumentation. This package is currently distributed as source / a release tarball; npm registry publication is not claimed.

```js
import OpenAI from 'openai';
import {createObserver} from '@cutbackai/sdk';

const observer = createObserver({
  enabled: true,
  endpoint: process.env.CUTBACK_ENDPOINT,
  projectId: process.env.CUTBACK_PROJECT_ID,
  serviceName: 'support-api',
  environment: 'staging',
  // Scoped ingestion credential comes from CUTBACK_PROJECT_TOKEN.
});
const client = new OpenAI({fetch: observer.fetch});
// An ordinary application call; use only with approved provider credentials/budget.
const response = await observer.run({workflowId: 'classify'}, () =>
  client.responses.create({model: 'gpt-4.1-mini', input: 'Your authorized input'}));
// At graceful shutdown / before a serverless invocation freezes:
await observer.close({timeoutMs: 100});
```

`run` returns the native SDK promise unchanged, including `.withResponse()` and `.asResponse()`. The SDK still owns retries, errors, async iteration and abort controllers. Each HTTP attempt has separate metadata. Attempts are linked only inside an explicit `run`; outside it, logical operation and retry attribution stay unknown. Never install this observer twice on one transport. Do not point `exportFetch` at `observer.fetch`.

The observer forwards response bytes as consumed. It retains at most 64 KiB of JSON or a bounded SSE frame and discards raw text after parsing; raw input/output, headers, error messages and provider request IDs are never exported. Missing/oversized/abandoned usage stays unknown. Unsupported modalities/tools remain unpriced. It does not enable content capture. Input shape checks are conservative and do not establish semantic safety or production quality.

Exporter queue and active observations are bounded (default 512); background export occurs every ten seconds, with a 300 ms timeout and no export retries. `flush`/`close` have explicit deadlines. A failed export drops telemetry and increments counters while inference continues. `health()` exposes local losses; periodic health reports use the collector's scoped ingestion API. Closing/frozen processes can still lose telemetry. An unconsumed response is settled as incomplete after 60 seconds; consumption after that deadline is forwarded but not re-metered.

Collector endpoints require HTTPS except loopback. Redirects are refused. Metadata identifiers and tokens are validated; disable collection with `enabled:false` (the default). Client configuration, provider secrets and transport remain application owned.

For a **no-charge** example with real collector/PostgreSQL transport, configure a staging project and scoped ingestion key, then run `node examples/synthetic-pipeline.mjs`. Both provider responses are intercepted locally and all provider network requests are refused. `npm test` exercises the installed native SDKs; `npm run check` validates their TypeScript promise/transport signatures. `npm pack` builds the installable archive.
