# A whole routing journey under one budget

This independent ticket classifier has two quality labels, a native baseline and
simple cheaper alternatives. The default synthetic small model fails some long
tickets, so the program must use its reserved training path, fit a router and
confirm once. The alternative scenario lets a simple model win and proves that
training is skipped. A third scenario removes usage to demonstrate an unknown
bill stopping execution without replay.

Use the matching current source; PyPI `0.1.0.dev5` lacks this feature. Docker
Desktop/Linux containers and Git must be available. From the Cutback checkout:

```sh
uv sync --locked --extra server --extra openai
uv run cutback collector up --target . --allow-payloads
uv run python examples/routing-program/sample.py --local-target .
uv run python examples/routing-program/sample.py --local-target . --simple-pass
uv run python examples/routing-program/sample.py --local-target . --unknown-billing
```

Every native response is intercepted. No provider key, paid inference, GitHub
token or private application is used. The declared baseline compute amount and
future opportunity are synthetic assumptions, not a customer benefit claim.

Inspect each returned workspace under ignored `.cutback/routing-program-example/`:

| Artifact | Meaning |
|---|---|
| `application/` | Independent original committed application and ignored agent journal |
| `proposed-application/` | Exact candidate configuration and local review ref, for passing scenarios |
| `proposal.json`, `change.patch`, `rollback.patch` | Current confirmed source diff and reversal |
| `checks.json`, `review.json` | Actual native consumer checks on both trees and declared coverage |
| `receipt.json` | Native request counts, known subtotal, chosen phases and unknown full-task costs |

Both passing scenarios execute the application's real SDK consumer with closed
test transports and verify byte-exact rollback. The script preserves the original
application and creates a proposed checkout; it never changes application traffic
or publishes a remote branch. The unknown-billing scenario produces no source
proposal and retains the full uncertain reservation.

Open the returned dashboard URL and select **Routing** to inspect the parent,
children and shared cost counters. Browser credentials remain in memory. Pass
`--cleanup` to remove only the sample's newly created synthetic project after the
run; private review files remain. When finished, stop the owned local stack:

```sh
uv run cutback collector down --target .
```

For an authorized real application, use the [program guide](../../docs/routing-programs.md).
Synthetic passes demonstrate control flow and native contracts, not real model
quality or realized savings. Router hosting, fitting dollars and full-task return
remain unknown.
