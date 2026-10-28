# Prompt releases and application experiments

Source-checkout functionality; not present in PyPI `0.1.0.dev5`. The registry is part of the optional PostgreSQL server. The offline scanner does not import a server, evaluation runtime or prompt client. [Independent synthetic example](../examples/prompt-releases/README.md).

## Author and inspect

In **Prompts**, create an identity under an explicit workflow, then save an immutable version under an active content policy. Version creation carries a client request UUID and expected latest ordinal. Repeating the exact request returns its original version; a changed request or stale head fails. Prompt bodies, schemas and fixed experiment variables are encrypted. Relational metadata has references, salted content hashes and audit fields. Default metadata exports do not contain prompt text.

Documents are either `text` plus one template, or `chat` plus ordered role/content messages. Declare every `{{variable}}` and its `string`, `integer`, `boolean` or `json` type. Missing/extra declarations and values fail. JSON values serialize deterministically. Inserted variable data is not reparsed as template syntax. `{{>component}}` references a declared alias pinned to an immutable text version. No Python, Jinja, expressions, file/network includes or tools execute. Composition is bounded to 32 versions, eight dependency edges deep, 20 variable names, 128 KB of graph documents and 64 KB of rendered output. Cycles, conflicting types and expansion overflow fail.

Output schemas belong to the root prompt. Reusable text components must be schema-free; composing a complete prompt with its own output contract as a partial is rejected instead of silently dropping that contract.

Content reads, render previews and diffs require a purpose and current rights to every dependency. They check both original and consumer workflow/environment scope, policy expiry, the global content switch, payload availability and the requested provider. Preview variables are explicitly authorized transient uploads and are not retained. The limited JSON Schema profile rejects references and regex evaluation. Schemas are validated separately from prompt composition.

## Resolve in an application

Install `[prompts]` for the optional HTTP client. Issue an environment-scoped `prompt_reader` key, then grant that principal exact prompt/policy/workflow/environment reads with an expiry and approval reference. A component needs its own grant. Ingest credentials remain write-only. Live OIDC membership/project grants are checked independently of token role claims. Revocation blocks later resolution and experiment calls.

```python
from cutback.prompts import PromptClient

# Supply these values from the application's explicit secret/configuration store.
with PromptClient(endpoint, project_id, reader_token) as prompts:
    release = prompts.resolve(
        "ticket-classifier",
        environment="staging",
        workflow="ticket.triage",
        provider="local",
        purpose="execute-reviewed-template",
        alias="staging",
    )
    rendered = release.render({"input": owned_request})
```

The resolver uploads no variable values, follows no redirects, uses no ambient proxies or provider credentials, makes no inference calls and has no disk cache or retry loop. It validates every encrypted-content receipt and graph hash. Resolve for each request when immediate current-policy checks are needed; a previously returned in-memory object cannot retroactively forget content. Local rendering rejects expiry. An alias is resolved to one exact version/revision; subsequent promotions do not mutate that returned graph.

## Compare under a frozen budget

The playground selects baseline/candidate versions, fixed variables, native models, a dataset/split, criteria and explicit request/dollar/output bounds. `input` is the declared string variable filled from each dataset case. Preparation compiles a declarative native contract and creates an encrypted plan requiring separate approval. No inference occurs in the browser or preparation endpoint. The existing durable worker reserves each request before execution and never retries a crashed potentially billable operation.

Sampling options are frozen per target: bounded temperature/top-p, Chat stop strings or Anthropic stop sequences. They cannot add tools, extra completions, destinations or output-budget overrides. A declared output schema adds a required schema criterion at threshold one; direct bound-plan creation cannot drop or weaken it. Multiple incompatible output schemas require a separate application-contract decision. Alternative schema-valid outputs remain valid even when their text differs. Schema validity is structural evidence, not semantic task quality.

Native compilation uses a qualified closed-object schema profile: explicit object properties, every property required (nullable types can represent optional values), `additionalProperties: false`, typed array items, scalar enums and bounded nested `anyOf`. It rejects format assertions, references, regex and unqualified composition keywords. OpenAI bounds for strings/numbers/arrays are retained; Anthropic's unsupported numeric/string/array bounds are rejected, never silently removed. The profile is capped at 1,000 properties, 2,000 schema nodes and ten nested edges. These checks follow the [OpenAI structured-output requirements](https://developers.openai.com/api/docs/guides/structured-outputs) and [Anthropic limitations](https://platform.claude.com/docs/en/build-with-claude/structured-outputs); real provider/model qualification is still separate. Provider-injected formatting prompts can add reported input usage; the ledger preserves actual usage and stops on unknown/out-of-bound billing rather than retrying.

Every bound model has an exact root version/hash, all dependency uses and its original content principal. Native contract equality is checked against the graph. Policies/grants/expiry/provider scope are rechecked before queueing, startup, between requests and before accepting results. Copied plans cannot extend content expiry or bypass later source authorization. Existing plans without prompt bindings retain their canonical serialization/hashes.

Pinned OpenAI 2.29.0 Responses and Chat contracts preserve text roles and approved structured output options. Anthropic 2.3.0 receives initial system messages in its native system field and bounded schemas through `output_config.format`; developer/interleaved system messages fail. This Anthropic SDK accepts stop sequences but no temperature/top-p arguments, so those are rejected before approval, including old unqualified request profiles that need fresh preparation. Local Chat execution uses the declared messages/options with the pinned OpenAI client, but each actual local server/model still needs operator qualification. No tools, streams, external prompt runtimes or arbitrary provider URLs are added by this feature. These are synthetic transport tests, not real-provider quality measurements.

## Promote, observe and roll back

An editor supplies exact expected alias revision/current version plus approval reference. Concurrent/stale promotions fail rather than overwrite a newer pointer. Alias history is immutable. A linked result must be a completed, billed, exact-version test run with current lineage and required gates. Synthetic/sample evidence remains unverified; a manual pointer change requires an explicit unverified-quality acknowledgment. Aliases do not deploy application code.

Rollback names the latest promotion receipt and revision, restores exactly its prior version and records a new receipt. It fails if the head changed or the old content is no longer authorized/available. Registry rollback and source-patch rollback are separate actions.

Applications may explicitly send the strict `PromptObservationBatch` to `/v1/ingest/prompt-observations` using their separate environment-scoped ingest credential. Reports contain version/hash, consumer/release, workflow, time, optional exact historical alias receipt and bounded status/duration. No prompts, inputs, outputs or free-form properties are accepted. Identity conflicts fail atomically. The impact view combines retained reports with known dependency/experiment uses. Coverage and unobserved consumers remain unknown; report absence is not zero usage or rollout success. Maintenance prunes observations with raw retention. Export/delete cover registry metadata, grants, uses and encrypted artifacts; lineage/history do not fabricate missing content.

Application behavior needs the separate [portable local checks](application-checks.md). Prompt evaluations alone do not prove shared consumers, tool side effects or production behavior remain correct.
