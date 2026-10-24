# Cutback

Discover AI call sites locally, without API keys or sending source to an LLM. An experimental metadata-only SDK and customer-owned collector/PostgreSQL pipeline are included for local development.

**Pre-alpha development release.** This is the initial foundation, not the complete optimization product. It does not yet run optimization experiments, verify semantic quality, provide a dashboard, or implement production deployment operations. Runtime contracts are tested with synthetic provider responses; live provider and production acceptance are not claimed. See [current status](https://github.com/CutbackAI/cutback/blob/main/docs/progress.md).

```sh
python -m pip install --pre cutback
cutback --help
cutback scan /path/to/your/python/project --format table
cutback scan /path/to/your/python/project --format json
```

The scanner discovers supported OpenAI and Anthropic Python call sites, model/source/prompt references, unresolved values, and a small offline pricing seed. Historical usage, actual spend, latency and quality remain unknown from source alone. It parses source without executing it. No account, provider key, paid request, or external control plane is needed.

For development from the repository:

```powershell
uv sync --all-extras --dev --locked
uv run cutback --help
uv run cutback doctor --json
uv run cutback scan examples/fastapi-mixed-providers --format json
uv run pytest
```

Optional extras: `cutback[openai]` (pinned experimental SDK adapter) and `cutback[server]` (self-hosted collector). Read the compatibility and operational limitations before enabling either.

See [quickstart](https://github.com/CutbackAI/cutback/blob/main/docs/quickstart.md), [local deployment](https://github.com/CutbackAI/cutback/blob/main/docs/deployment.md), [provider compatibility](https://github.com/CutbackAI/cutback/blob/main/docs/providers.md), and [security](https://github.com/CutbackAI/cutback/security/policy). Apache-2.0. No calls home or implicit paid inference. Independent of any commercial account or cloud vendor.

