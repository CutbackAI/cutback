# A cache that refuses a tempting wrong answer

Run this independent synthetic walkthrough from the repository:

```shell
uv sync --locked --all-extras --dev
uv run cutback demo --semantic-cache
```

Docker Desktop and Git are required. No model key or paid inference is needed. Choose **Explore sample → Semantic caching**. Inspect the frozen namespace, validation-only threshold selection, separate confirmation evidence and activated curated catalog. The low threshold accepts an intentionally wrong neighbor; the selected high threshold refuses it. Complete task costs and realized savings remain unknown.

The [fixture source](../../src/cutback/sample_app/semantic_cache_fixture.py) contains 500 symbolic vector controls. The [HTTP walkthrough](../../src/cutback/demo_semantic_cache.py) uploads encrypted chunks, finalizes one dataset without copying its payload objects, advances the real worker and tests both a hit and a refused match. This proves workflow mechanics, not embedding quality or real-world independence. The source feature is still under qualification and is absent from PyPI dev5.

For an actual application, supply its separately authorized vectors, independent labels, known fallback outputs, meaningful intent groups and access partition. Follow the [operator and CLI guide](../../docs/semantic-cache.md), then qualify its own untouched confirmation controls. Never substitute this fixture's outcome for the application's evidence.
