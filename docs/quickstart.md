# Quickstart

Start with [the complete support-desk walkthrough](testing-the-sample.md). It explains installation, expected results, the dashboard and cleanup. No provider key, private application or paid inference is needed.

For source-only scanning, use `cutback sample support-desk`, then `cutback scan support-desk` and `cutback opportunities --target support-desk`. Scanning works without Docker.

## Try the automatic improvement from source

Use a source checkout for features added after PyPI dev5. With Python 3.11–3.13, Git, uv and running Docker Desktop:

```sh
git clone https://github.com/CutbackAI/cutback.git
cd cutback
uv sync --locked --all-extras --dev
uv run --locked cutback demo --ticket-router
```

Open the printed local dashboard and click **Explore sample**. In **Three labels. One automatic improvement.**, choose **Inspect ticket-routing failures**. The app must choose billing, access or technical; a cheap constant billing answer fails the other categories. Follow the ticket-routing campaign to inspect the frozen model/output alternatives, rejected attempt, reserved instruction search, separate confirmation, application checks and reversible source change. No key is needed: actual SDK/collector/database/worker operations use synthetic responses. The 240 rows belong to 60 declared families, not 240 independent tasks or a real-model benchmark.

Stop the command with Ctrl+C to remove its owned database and encrypted objects. To inspect the separate asynchronous lifecycle, run `uv run --locked cutback demo --provider-batch` from source containing that increment and choose **Inspect the native batch lifecycle**. Its [qualification state](progress.md) and [storage/cancellation limits](provider-batches.md) remain explicit. Both commands use the packaged dashboard, so Node is unnecessary for exploring them.

For your application, follow [application onboarding](application-onboarding.md). For deployment and operation, use [deployment](deployment.md). Maintainer-only commands and browser prerequisites are in the walkthrough and [contributing](../CONTRIBUTING.md).
