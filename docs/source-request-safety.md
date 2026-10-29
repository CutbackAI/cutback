# Source request fidelity

Source contracts are static, parse-only evidence. They do not execute the application, inspect live process globals or prove complete runtime coverage. An unresolved request remains available for discovery/observation; it cannot silently become a replayable request with guessed values.

## Bind the value the application actually refers to

A function argument shadows a same-named module variable, even when the argument has a default. Local loop/import/context-manager/deletion bindings, closure arguments and `global`/`nonlocal` declarations also affect lookup. A module alias or `Path` reader can be shadowed too. The profile compiler rejects these unsupported scopes rather than borrowing an unrelated global literal.

For example, the following instruction is runtime-dependent:

```python
INSTRUCTION = "module default"


def answer(text, INSTRUCTION):
    return client.responses.create(
        model="gpt-4.1",
        input=text,
        instructions=INSTRUCTION,
        max_output_tokens=64,
    )
```

It must not be replayed using `"module default"`. Continue metadata observation and inspect the actual request through an authorized adapter before preparing a source-bound replay. This does not authorize capturing content or running inference.

Parent-relative imports resolve against their actual package level. A sibling with the same filename cannot substitute for a parent module. Package-relative qualification requires an identifiable package marker; ambiguous root/`src` imports, wildcard/duplicate aliases and circular import chains stop with a bounded review-required result. Lambda/class-body requests and shadowed generic type parameters are not treated as ordinary straight-line function bindings. These are explicit adapter limits, not exhaustive Python interpretation.

## Check the proposed request, not only its literal diff

A model constant may also feed an instruction, input prefix or another request option. Changing that constant can alter more than the model that was evaluated. Before returning a patch, Cutback recompiles its proposed source in an in-memory overlay and compares the model, provider, instruction, output ceiling and complete request-template/options fingerprint with the evaluated candidate. A mismatch or newly unresolved source rejects the proposal with `PATCH_CHANGED_UNEVALUATED_REQUEST_CONTRACT`.

The overlay reads only the existing bounded workspace dependencies, respects path/symlink/file-size limits, and writes no application file. It is not a sandbox or an application test. Application checks, affected-consumer review, fresh held-out evidence and deployment approval remain separate requirements. A candidate that fails this guard needs a separately qualified source transformation; the engine does not silently change its evaluated request to make the patch fit.

Unambiguous existing `request-profile-v1` serialization and approval hashes stay unchanged. Previously frozen native experiment inputs and budgets are not rewritten. Fresh source inspection/proposal generation may now reject an older ambiguous binding; that requires new source evidence, not resetting or replaying a consumed approval. [Actual acceptance and package availability](progress.md).
