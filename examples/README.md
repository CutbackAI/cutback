# Examples

**Start with the [support-desk application](support_desk/README.md): `uv run cutback demo`.** Its [walkthrough](../docs/testing-the-sample.md) shows a useful end-to-end decision before you study SDK/framework details. No provider key or paid call is needed.

The examples below are supplementary compatibility and integration references.

Source checkouts also include [whole-workflow investigation](workflow-tracing/README.md): four synthetic agent/retrieval/tool/LLM steps, a failed tool and a delayed external score, viewed through real PostgreSQL and the Traces dashboard. This is separate from PyPI dev5's native single-call demo.

The [import and blind-review walkthrough](review-workflow/README.md) uses a real pinned Promptfoo producer fixture, encrypted preview, separate reviewer sessions, disagreement/adjudication and promotion to a new frozen dataset. It runs no inference. Captured trace steps can also start this workflow from **Traces → Prepare review of this step**. These features require a source checkout.

Start from the repository with `uv sync --locked --all-extras --dev`. The runnable examples below use the **installed pinned native SDKs**, with provider HTTP intercepted by explicitly synthetic transports. They make no paid provider request or model download. Framework development servers bind loopback; they are examples, not production deployments.

| Example | Run | What it demonstrates |
|---|---|---|
| Native sync, async and streams | `uv run python -m examples.runnable.sync_async_streams` | OpenAI Responses/Chat and Anthropic Messages; native stream/context-manager behavior |
| Bounded application worker | `uv run python -m examples.runnable.worker` | Two local worker threads, explicit workflow context and graceful shutdown |
| Lambda-style handler | `uv run python -m examples.runnable.lambda_handler` | A real handler return value and a bounded best-effort flush before invocation freeze |
| Django view | `uv run python -m examples.runnable.django_demo` | Actual Django request/view with a native synthetic SDK client |
| Django local server | `uv run python -m examples.runnable.django_demo --serve` | Loopback development server at `http://127.0.0.1:8001/synthetic` |
| FastAPI mixed-provider app | `uv run uvicorn examples.runnable.fastapi_app:app --host 127.0.0.1 --port 8000` | Lifespan-managed async clients; bounded `POST /classify` with `{"text":"owned synthetic example"}` |
| Full metadata pipeline | `uv run python scripts/demo.py` | Native SDK → actual Compose HTTP collector → PostgreSQL → CLI receipt |
| Full experiment workflow | `uv run python examples/experiment_walkthrough.py` | Scanner → native SDK observation → collector/PostgreSQL → encrypted datasets → hash approval → durable worker → paired reports |
| Node metadata pipeline | `node packages/js-sdk/examples/synthetic-pipeline.mjs` | Pinned native Node SDKs → actual collector/PostgreSQL, with an explicitly configured local project/key |

The last three require the local stack from [the quickstart](../docs/quickstart.md). The experiment walkthrough starts a temporary real collector against the local Compose PostgreSQL, creates its own rights-approved synthetic fixtures and encrypted store, records 64 synthetic native observations and two comparisons, saves metadata evidence under ignored `artifacts/`, then deletes **only its own** project and encrypted objects. It shows independent synthetic labels separately from teacher references. Synthetic examples do not verify representative customer quality. Its inference budget is a reservation of modeled fixture usage, not a paid request authorization.

The Node example uses `CUTBACK_PROJECT_ID`, `CUTBACK_PROJECT_TOKEN` and an optional loopback `CUTBACK_ENDPOINT`. No credentials are printed. Obtain a scoped ingestion key in the dashboard or CLI. Install the exact supported Node SDK versions with `npm ci --ignore-scripts` in `packages/js-sdk`; see its [compatibility/transport contract](../packages/js-sdk/README.md).

Application examples keep observation disabled unless integrated through an explicitly authorized collector bootstrap. To observe an actual application, add `cutback.init` with its own service/environment/project key and collector endpoint. Use `workflow` for business attribution; source linkage stays unknown unless the correct source call-site identity is supplied. Follow [provider limits](../docs/providers.md), [privacy rules](../docs/security-and-privacy.md) and the [staging Lambda bootstrap](aws-lambda/staging_bootstrap.py). Running a handler locally does not prove AWS network/freeze behavior.

Directories `minimal-openai`, `minimal-anthropic` and `fastapi-mixed-providers` are **scan-only source fixtures**. They illustrate static resolution and may require SDK credentials if imported; Cutback scanning does not import or execute them. Use the runnable examples above for a no-charge first execution:

```sh
cutback scan examples/fastapi-mixed-providers --json
cutback scan packages/js-sdk --language javascript --language typescript --json
```

Changing a synthetic transport to a real provider changes the authorization boundary. Approve permitted data/vendors/credentials and a dollar cap first; use the frozen experiment runner for bounded comparisons. Do not treat fixture performance, requested caching, or model training loss as realized savings or verified business quality.
