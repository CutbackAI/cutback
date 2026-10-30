# Qualify a semantic cache before serving answers

This source feature is under qualification and is **not in PyPI dev5**. It reuses a curated answer from supplied dense vectors. It does not generate embeddings, silently invoke a model, or establish that similar questions have interchangeable answers.

## Run the independent example

From a matching source checkout, with Docker Desktop running and Git available:

```shell
uv sync --locked --all-extras --dev
uv run cutback demo --semantic-cache
```

Choose **Explore sample**, then **Semantic caching**. The sample uses actual scoped HTTP, PostgreSQL, encrypted case objects, chunked dataset upload, durable local workers and cache serving. Every vector, label and baseline answer is synthetic; no provider credential or paid inference call is needed. Stop with Ctrl+C to stop its temporary database.

The deliberately unsafe threshold `0.5` accepts misleading neighbors and fails the utility and false-hit gates. Calibration chooses a passing threshold before a separate confirmation set is inspected. The frozen `0.99` control rejects the unsafe match. These symbolic controls test the mechanism; they are not a benchmark of an embedding model, real customer quality, or population independence.

## Connect an independent application

1. Enable the operator-controlled `CUTBACK_ALLOW_PAYLOADS=true` and `CUTBACK_SEMANTIC_CACHE_ENABLED=true` only with a configured encrypted store. Migrate PostgreSQL before starting the updated server. The scanner remains offline and independent of these extras.
2. Approve a capture policy covering the workflow, environment and provider. Create a scoped ingest key. In **Semantic caching**, create a namespace with that key, exact embedding adapter/revision/dimension, model, prompt revision, knowledge freshness, authorization-context SHA256 and expiry. Scope the context to the application's actual tenant and answer permissions. The application supplies this partition; similarity cannot discover permissions.
3. Curate independent training answers with the assigned key. Catalog entries are immutable. Queries, vectors and responses remain in encrypted objects, not index metadata. Freeze catalog membership before qualification; changing/expiring any member invalidates serving. A new catalog needs a new namespace and qualification.
4. Import independently labeled query controls, including tempting wrong neighbors, known fallback outputs, duplicate/paraphrase groups and the exact embedding contract. Keep training/catalog families separate from validation and confirmation. The system conservatively merges normalized query aliases; varying an ID, vector or fallback output cannot manufacture independent query coverage.
5. Prepare a threshold grid and required gates. A current administrator approves its exact hash once. Run the optional worker with `CUTBACK_SEMANTIC_CACHE_EXECUTION=true`. This enables deterministic upload finalization and cache qualification; it does not authorize native inference.
6. Review the validation curve, confirmation aggregates, false-hit upper bound, useful-hit groups, missing baseline coverage and group-weighted task utility. A current administrator separately approves the full report-bound activation hash. A failed or unknown gate cannot activate. Activation is specific to the frozen catalog and current capture policy.
7. Submit a vector with the exact assigned ingest credential, namespace, access-context hash and embedding-contract hash. Only a qualified current hit contains a response. Treat misses as ordinary application fallback decisions; Cutback does not perform that fallback or retry a paid request.

The dashboard keeps credentials and selected content in tab memory. Explicit purpose-authorized catalog review is available to administrators through `POST .../namespaces/{id}/entries/{entry_id}/content`; metadata viewers do not receive raw content. Revoke a namespace to stop serving immediately. Explicit namespace deletion removes its catalog and abandoned staging objects; finalized datasets retain their separate capture-policy lifecycle. Project deletion includes all cache/upload metadata and encrypted objects.

## Dataset transport and recovery

Use one ordinary `DatasetImport` JSON file with at most 512 cases and 32 MiB. The browser accepts flat string-labeled cases; the CLI validates the complete import contract. The collector's existing 1 MiB request limit is unchanged. The upload reserves slots against the same policy allowance used by samples, exact caches and other captures; chunks contain at most eight cases and adapt to the request bound.

```shell
cutback semantic-cache upload-dataset controls.json NAMESPACE_UUID \
  --project PROJECT_UUID --endpoint http://127.0.0.1:8766 \
  --authorize-content-upload --output .cutback/upload-receipt.json
```

This uses `CUTBACK_CONTROL_TOKEN`; never put a credential in the command line, source or URLs. The receipt is saved before sending content. If an acknowledgement is lost, inspect its upload UUID and resume the **same** file using `--resume-upload UPLOAD_UUID`. Identical offsets do not write duplicate objects; conflicting content is refused. Finalization atomically reuses the registered encrypted cases and creates one dataset. Inspect `upload-status`; `FINALIZING` is not a completed import. Cancel with `cancel-upload` and the exact approval hash. Abandoned staging payloads expire within an hour, or sooner under namespace/key/policy expiry.

Each finalizer attempt is bounded to at most 120 active seconds; the default is one attempt. A second attempt requires that bound to be explicitly frozen at preparation. A lost finalization commit acknowledgement is reconciled against the stored dataset ID. This is deterministic content processing, with no inference calls; storage and transfer costs remain unknown.

Each case input is a JSON **string**, for example:

```json
{
  "id": "independent-control-a",
  "group": "independent-intent-a",
  "input": "{\"query\":\"Independent authorized question\",\"vector\":[1.0,0.0],\"embedding_contract_sha256\":\"EXACT_NAMESPACE_EMBEDDING_HASH\",\"baseline_status\":\"completed\",\"baseline_output\":\"billing\"}",
  "gold": "billing"
}
```

Use the exact real hash and dimensions, not this illustrative placeholder. A baseline may instead be explicitly `failed`, or `unknown` with no output. Unknown fallback quality cannot pass the utility gate. The index supports 1–2,048 dimensions and at most 256 curated answers; this is an exhaustive bounded catalog, not a distributed vector database.

## Evidence and limits

The report records TP/FP/TN/FN, hit precision/ratio, declared positive/negative groups, any-bad-hit-per-negative-group Wilson upper 95% bound, useful-hit groups and group-weighted task-utility change. The default minimums are 30 negative and 10 positive groups **in each assessed split**. A zero-hit cache or positive-only dataset cannot establish useful safe reuse. Declared reference rights, embedding provenance and group independence are owner assertions; the system does not independently certify them.

Embedding cost, hosting cost, complete application cost and realized savings stay unknown. Local lookup processing time is not native inference latency or end-to-end task latency. Confirmation does not retune the threshold. Durable validation/test exposure prevents reusing previously observed holdouts as fresh evidence. Interrupted qualification fails without a fresh selection or automatic paid retry. Revoked keys, changed policies, expired evidence, ambiguous different answers, malformed/partial catalogs and unavailable content cannot serve an answer.

The design follows the distinction between [semantic retrieval and cache scoping/freshness](https://redis.io/docs/latest/develop/use-cases/semantic-cache/), [ranking and operational threshold utility](https://arxiv.org/abs/2606.19719v4), and [similarity and answer validity](https://arxiv.org/abs/2609.35908v1). Those sources motivate controls; their measured results are not Cutback results.
