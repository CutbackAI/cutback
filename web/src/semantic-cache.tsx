import React, { useCallback, useEffect, useRef, useState } from "react";
import type { components } from "./api.generated";
import { type API, Field, Select, explain, json, values } from "./ui";
import { normalizeCases, nextChunk, sequenceHash } from "./semantic-upload";

type Namespace = components["schemas"]["NamespaceReceipt"];
type Qualification = components["schemas"]["QualificationReceipt"];
type Upload = components["schemas"]["SemanticUploadReceipt"];

export function SemanticCache({
  api,
  project,
  canWrite,
  canApprove,
}: {
  api: API;
  project: string;
  canWrite: boolean;
  canApprove: boolean;
}) {
  const base = `/projects/${project}/semantic-cache`;
  const [rows, setRows] = useState<Namespace[]>([]);
  const [qualifications, setQualifications] = useState<Qualification[]>([]);
  const [datasets, setDatasets] = useState<any[]>([]);
  const [policies, setPolicies] = useState<any[]>([]);
  const [keys, setKeys] = useState<any[]>([]);
  const [selected, setSelected] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [offset, setOffset] = useState(0);
  const alive = useRef(true);
  const serial = useRef(0);
  const load = useCallback(async () => {
    const request = ++serial.current;
    const [namespaces, receipts, available, storage, scopedKeys] =
      await Promise.all([
        api(`${base}/namespaces?limit=25&offset=${offset}`),
        api(`${base}/qualifications?limit=100&offset=0`),
        api(`/projects/${project}/datasets?limit=100&offset=0`),
        canApprove
          ? api(`/projects/${project}/capture-policies`)
          : Promise.resolve([]),
        canApprove ? api(`/projects/${project}/keys`) : Promise.resolve([]),
      ]);
    if (!alive.current || serial.current !== request) return;
    setRows(namespaces);
    setQualifications(receipts);
    setDatasets(available);
    setPolicies(storage.filter((value: any) => value.enabled));
    setKeys(
      scopedKeys.filter(
        (value: any) => value.role === "ingest" && !value.revoked_at,
      ),
    );
  }, [api, project, base, offset, canApprove]);
  useEffect(() => {
    alive.current = true;
    load().catch((error) => {
      if (alive.current) setMessage(explain(error));
    });
    return () => {
      alive.current = false;
      serial.current++;
    };
  }, [load]);
  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    setMessage("");
    try {
      await action();
      if (alive.current) {
        await load();
        if (alive.current)
          setMessage("Saved. Refresh to inspect worker progress.");
      }
    } catch (error) {
      if (alive.current) setMessage(explain(error));
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  const ns = rows.find((row) => row.id === selected);
  return (
    <section aria-label="Semantic cache qualification">
      <h2>Semantic caching</h2>
      <p>
        Reuse a curated answer only after its frozen threshold passes
        calibration and a separate confirmation set. Similarity alone cannot
        prove correctness.
      </p>
      <ol>
        <li>Authorize a namespace and curate its encrypted answer catalog.</li>
        <li>
          Upload labeled queries with supplied embeddings and known fallback
          outputs.
        </li>
        <li>
          Review the quality gates, approve local qualification, then explicitly
          activate passing evidence.
        </li>
      </ol>
      <p>
        Embedding generation is supplied by your application. Embedding,
        hosting, complete task cost and realized savings remain unknown here.
      </p>
      <button type="button" disabled={busy} onClick={() => act(load)}>
        Refresh semantic caches
      </button>
      {message && <p role="status">{message}</p>}
      {canApprove && (
        <NamespaceForm
          policies={policies}
          keys={keys}
          busy={busy}
          submit={(body) =>
            act(async () => {
              const created = await json(api, `${base}/namespaces`, body);
              if (alive.current) setSelected(created.id);
            })
          }
        />
      )}
      <Select
        name="semantic-namespace"
        label="Semantic namespace"
        value={selected}
        onChange={(event) => setSelected(event.target.value)}
      >
        <option value="">Choose a namespace</option>
        {rows.map((row) => (
          <option key={row.id} value={row.id}>
            {String(row.specification.workflow)} · {row.id.slice(0, 8)} ·{" "}
            {row.state}
          </option>
        ))}
      </Select>
      {!rows.length && <p>No semantic namespaces on this page.</p>}
      <div className="actions">
        <button
          disabled={busy || offset === 0}
          onClick={() => {
            setSelected("");
            setOffset(Math.max(0, offset - 25));
          }}
        >
          Previous namespaces
        </button>
        <button
          disabled={busy || rows.length < 25 || offset >= 1000}
          onClick={() => {
            setSelected("");
            setOffset(offset + 25);
          }}
        >
          Next namespaces
        </button>
      </div>
      {ns && (
        <NamespaceWorkspace
          key={ns.id}
          api={api}
          base={base}
          project={project}
          ns={ns}
          datasets={datasets}
          qualifications={qualifications.filter(
            (row) => row.namespace_id === ns.id,
          )}
          canWrite={canWrite}
          canApprove={canApprove}
          busy={busy}
          act={act}
        />
      )}
    </section>
  );
}

function NamespaceForm({
  policies,
  keys,
  busy,
  submit,
}: {
  policies: any[];
  keys: any[];
  busy: boolean;
  submit: (body: unknown) => void;
}) {
  const [consent, setConsent] = useState(false);
  return (
    <details className="editor">
      <summary>Create a semantic namespace</summary>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          const input = values(event.currentTarget);
          submit({
            policy_id: input.policy,
            serving_key_id: input.key,
            workflow: input.workflow,
            environment: input.environment,
            provider: input.provider,
            model: input.model,
            prompt_revision: input.prompt,
            freshness_ref: input.freshness,
            access_context_sha256: input.context,
            rule: {
              embedding: {
                adapter: input.adapter,
                revision_sha256: input.revision,
                dimensions: Number(input.dimensions),
              },
              threshold: 0.99,
            },
            max_entries: Number(input.entries),
            max_lookups_per_minute: 30,
            expires_at: new Date(
              Date.now() + Number(input.minutes) * 60000,
            ).toISOString(),
            curated_catalog_approved: consent,
            encrypted_storage_approved: consent,
          });
        }}
      >
        <Select
          name="policy"
          label="Approved encrypted-content policy"
          required
        >
          <option value="">Choose a policy</option>
          {policies.map((row) => (
            <option key={row.id} value={row.id}>
              {row.policy.approval_ref} · {row.id.slice(0, 8)}
            </option>
          ))}
        </Select>
        <Select name="key" label="Assigned serving key" required>
          <option value="">Choose an ingest key</option>
          {keys.map((row) => (
            <option key={row.id} value={row.id}>
              {row.id}
            </option>
          ))}
        </Select>
        <div className="form-grid">
          <Field
            name="workflow"
            label="Cache workflow"
            required
            defaultValue="classify"
          />
          <Field
            name="environment"
            label="Cache environment"
            required
            defaultValue="staging"
          />
          <Select
            name="provider"
            label="Fallback provider"
            defaultValue="openai"
          >
            <option>openai</option>
            <option>anthropic</option>
            <option>local</option>
          </Select>
          <Field name="model" label="Fallback model" required />
          <Field name="prompt" label="Prompt revision" required />
          <Field
            name="freshness"
            label="Knowledge freshness revision"
            required
          />
          <Field
            name="context"
            label="Authorization-context SHA256"
            required
            pattern="[a-f0-9]{64}"
            hint="Bind your application's tenant, user permissions and answer scope. Similarity cannot infer access rights."
          />
          <Field name="adapter" label="Embedding adapter identifier" required />
          <Field
            name="revision"
            label="Embedding revision SHA256"
            required
            pattern="[a-f0-9]{64}"
          />
          <Field
            name="dimensions"
            label="Embedding dimensions"
            type="number"
            min={1}
            max={2048}
            defaultValue={1536}
            required
          />
          <Field
            name="entries"
            label="Maximum curated answers"
            type="number"
            min={1}
            max={256}
            defaultValue={128}
            required
          />
          <Field
            name="minutes"
            label="Namespace lifetime in minutes"
            type="number"
            min={1}
            max={43200}
            defaultValue={60}
            required
          />
        </div>
        <label>
          <input
            type="checkbox"
            checked={consent}
            onChange={(event) => setConsent(event.target.checked)}
          />{" "}
          I authorize this curated catalog and encrypted storage under the
          selected policy.
        </label>
        <button disabled={busy || !consent} type="submit">
          Create authorized namespace
        </button>
      </form>
    </details>
  );
}

function NamespaceWorkspace({
  api,
  base,
  project,
  ns,
  datasets,
  qualifications,
  canWrite,
  canApprove,
  busy,
  act,
}: {
  api: API;
  base: string;
  project: string;
  ns: Namespace;
  datasets: any[];
  qualifications: Qualification[];
  canWrite: boolean;
  canApprove: boolean;
  busy: boolean;
  act: (action: () => Promise<unknown>) => Promise<void>;
}) {
  const [ingestKey, setIngestKey] = useState("");
  const [entryFile, setEntryFile] = useState<File | null>(null);
  const [datasetFile, setDatasetFile] = useState<File | null>(null);
  const [uploadConsent, setUploadConsent] = useState(false);
  const [upload, setUpload] = useState<Upload | null>(null);
  const [resumeId, setResumeId] = useState("");
  const [lookupFile, setLookupFile] = useState<File | null>(null);
  const [lookup, setLookup] = useState<any>(null);
  const [deleteHash, setDeleteHash] = useState("");
  const [deleteRef, setDeleteRef] = useState("");
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  function current() {
    if (!live.current) throw new Error("SEMANTIC_CACHE_CONTEXT_CHANGED");
  }
  async function read(file: File | null, bound: number) {
    if (!file || file.size > bound)
      throw new Error("SEMANTIC_UPLOAD_FILE_BYTE_BOUND");
    const value = JSON.parse(await file.text());
    current();
    return value;
  }
  async function sendDataset() {
    const value = await read(datasetFile, 33554432);
    const cases = normalizeCases(value.cases);
    const hash = await sequenceHash(cases);
    current();
    const { cases: ignored, ...metadata } = value;
    if (
      metadata.policy_id !== ns.specification.policy_id ||
      metadata.workflow !== ns.specification.workflow ||
      (metadata.environment ?? "staging") !== ns.specification.environment
    )
      throw new Error("SEMANTIC_UPLOAD_METADATA_SCOPE_MISMATCH");
    let receipt: Upload = resumeId
      ? await api(`${base}/dataset-uploads/${encodeURIComponent(resumeId)}`)
      : await json(api, `${base}/namespaces/${ns.id}/dataset-uploads`, {
          metadata,
          expected_cases: cases.length,
          expected_cases_sha256: hash,
          content_upload_approved: uploadConsent,
          max_finalize_seconds: 120,
        });
    current();
    setUpload(receipt);
    setResumeId(receipt.id);
    const frozen = receipt.specification.upload as {
      expected_cases_sha256: string;
      expected_cases: number;
    };
    if (
      receipt.namespace_id !== ns.id ||
      frozen.expected_cases_sha256 !== hash ||
      frozen.expected_cases !== cases.length
    )
      throw new Error("SEMANTIC_UPLOAD_RESUME_CONTENT_OR_SCOPE_MISMATCH");
    if (["FINALIZING", "FINALIZED"].includes(receipt.state)) return;
    if (receipt.state !== "STAGING")
      throw new Error("SEMANTIC_UPLOAD_NOT_RESUMABLE");
    while (receipt.received_cases < cases.length) {
      current();
      const offset = receipt.received_cases;
      const chunk = nextChunk(cases, offset);
      receipt = await json(
        api,
        `${base}/dataset-uploads/${receipt.id}/append`,
        {
          approval_hash: receipt.approval_hash,
          offset,
          cases: chunk,
        },
      );
      current();
      setUpload(receipt);
      if (receipt.received_cases !== offset + chunk.length)
        throw new Error("SEMANTIC_UPLOAD_UNEXPECTED_CURSOR");
    }
    receipt = await json(
      api,
      `${base}/dataset-uploads/${receipt.id}/finalize`,
      { approval_hash: receipt.approval_hash },
    );
    current();
    setUpload(receipt);
  }
  const spec = ns.specification as any;
  return (
    <section aria-label={`Semantic namespace ${ns.id.slice(0, 8)}`}>
      <h3>
        Namespace {ns.id.slice(0, 8)} · {ns.state}
      </h3>
      <p>
        {ns.entry_count} curated answers · expires{" "}
        {new Date(ns.expires_at).toLocaleString()}
      </p>
      <p>
        Namespace contract <code>{ns.specification_hash}</code>
      </p>
      <p>
        Embedding contract <code>{ns.embedding_contract_sha256}</code>
      </p>
      <details>
        <summary>Frozen namespace scope</summary>
        <pre>{JSON.stringify(ns.specification, null, 2)}</pre>
      </details>
      <CuratedCatalog
        api={api}
        base={base}
        ns={ns}
        canApprove={canApprove}
        busy={busy}
        act={act}
      />
      {canWrite && ns.state === "ACTIVE" && (
        <section aria-label="Authorized cache lookup">
          <h4>Check an authorized vector</h4>
          <p>
            This sends only your supplied vector to the frozen namespace. A miss
            performs no fallback inference. The credential and any returned
            answer stay in this tab's memory.
          </p>
          <Field
            name="active-serving-token"
            label="Assigned ingest credential"
            type="password"
            autoComplete="off"
            value={ingestKey}
            onChange={(event) => setIngestKey(event.target.value)}
          />
          <Field
            name="lookup-vector"
            label="Lookup vector JSON file"
            type="file"
            accept="application/json,.json"
            onChange={(event) => setLookupFile(event.target.files?.[0] ?? null)}
          />
          <button
            disabled={busy || !ingestKey || !lookupFile}
            onClick={() =>
              act(async () => {
                const content = await read(lookupFile, 900000);
                const result = await json(
                  (path, options) =>
                    api(path, {
                      ...options,
                      headers: { Authorization: "Bearer " + ingestKey },
                    }),
                  "/ingest/semantic-cache/lookup",
                  {
                    vector: content.vector,
                    project_id: project,
                    namespace_id: ns.id,
                    access_context_sha256: spec.access_context_sha256,
                    embedding_contract_sha256: ns.embedding_contract_sha256,
                  },
                );
                current();
                setLookup(result);
              })
            }
          >
            Run scoped cache lookup
          </button>
          {lookup && (
            <>
              <p>
                {lookup.hit ? "Authorized cache hit" : "Cache miss"}. Realized
                savings: unknown.
              </p>
              <pre>{JSON.stringify(lookup, null, 2)}</pre>
            </>
          )}
        </section>
      )}
      {canWrite && ns.state === "DRAFT" && (
        <>
          <h4>1. Curate the catalog</h4>
          <p>
            Upload one JSON object with query, response, vector and ttl_seconds.
            The assigned ingest credential stays in this tab's memory.
          </p>
          <Field
            name="serving-token"
            label="Assigned ingest credential"
            type="password"
            autoComplete="off"
            value={ingestKey}
            onChange={(event) => setIngestKey(event.target.value)}
          />
          <Field
            name="entry"
            label="Curated answer JSON file"
            type="file"
            accept="application/json,.json"
            onChange={(event) => setEntryFile(event.target.files?.[0] ?? null)}
          />
          <button
            disabled={busy || !ingestKey || !entryFile}
            onClick={() =>
              act(async () => {
                const content = await read(entryFile, 900000);
                await json(
                  (path, options) =>
                    api(path, {
                      ...options,
                      headers: { Authorization: "Bearer " + ingestKey },
                    }),
                  "/ingest/semantic-cache/put",
                  {
                    ...content,
                    project_id: project,
                    namespace_id: ns.id,
                    access_context_sha256: spec.access_context_sha256,
                    embedding_contract_sha256: ns.embedding_contract_sha256,
                  },
                );
              })
            }
          >
            Store curated answer
          </button>
          <h4>2. Upload independent query controls</h4>
          <p>
            Use a DatasetImport JSON file with at most 512 flat labeled cases
            and 32 MiB. Each case input contains query, vector,
            embedding_contract_sha256, baseline_status and baseline_output. The
            original 1 MiB request limit stays enforced.
          </p>
          <Field
            name="dataset"
            label="Semantic dataset JSON file"
            type="file"
            accept="application/json,.json"
            onChange={(event) =>
              setDatasetFile(event.target.files?.[0] ?? null)
            }
          />
          <Field
            name="resume"
            label="Resume upload UUID (optional)"
            value={resumeId}
            onChange={(event) => setResumeId(event.target.value)}
          />
          <label>
            <input
              type="checkbox"
              checked={uploadConsent}
              onChange={(event) => setUploadConsent(event.target.checked)}
            />{" "}
            I authorize uploading these queries, labels, vectors and fallback
            outputs to the approved encrypted store.
          </label>
          <button
            disabled={busy || !datasetFile || !uploadConsent}
            onClick={() => act(sendDataset)}
          >
            Upload and seal dataset
          </button>
        </>
      )}
      {upload && (
        <section aria-label="Encrypted dataset upload">
          <p>
            {upload.state}: {upload.received_cases} cases ·{" "}
            {upload.received_bytes} bytes
          </p>
          <p>
            Upload UUID <code>{upload.id}</code>. Keep this metadata UUID to
            resume the same file after an interrupted append.
          </p>
          <p>
            Billable inference calls: {upload.billable_inference_calls}. Storage
            and transfer cost: unknown.
          </p>
          {upload.dataset_id && (
            <p>
              Finalized dataset <code>{upload.dataset_id}</code>
            </p>
          )}
          {upload.failure_code && (
            <p role="alert">{explain(new Error(upload.failure_code))}</p>
          )}
          <button
            disabled={busy}
            onClick={() =>
              act(async () => {
                const value = await api(`${base}/dataset-uploads/${upload.id}`);
                current();
                setUpload(value);
              })
            }
          >
            Refresh upload status
          </button>
          {canWrite && !["FINALIZED", "CANCELLED"].includes(upload.state) && (
            <button
              disabled={busy}
              onClick={() =>
                act(async () => {
                  const value = await json(
                    api,
                    `${base}/dataset-uploads/${upload.id}/cancel`,
                    { approval_hash: upload.approval_hash },
                  );
                  current();
                  setUpload(value);
                })
              }
            >
              Cancel encrypted upload
            </button>
          )}
        </section>
      )}
      {canWrite && ns.state === "DRAFT" && (
        <QualificationForm
          ns={ns}
          datasets={datasets}
          busy={busy}
          submit={(body) =>
            act(() =>
              json(api, `${base}/namespaces/${ns.id}/qualifications`, body),
            )
          }
        />
      )}
      <h4>Qualification evidence</h4>
      <p>
        Showing up to the latest 100 project receipts. Quality refers to
        supplied controls, not verified customer outcomes. Refresh to inspect
        worker progress.
      </p>
      {qualifications.map((row) => (
        <QualificationReview
          key={row.id}
          row={row}
          api={api}
          base={base}
          canApprove={canApprove}
          busy={busy}
          act={act}
        />
      ))}
      {!qualifications.length && (
        <p>No qualification receipts in this window.</p>
      )}
      {canApprove && ns.state !== "REVOKED" && (
        <button
          disabled={busy}
          onClick={() =>
            act(() => json(api, `${base}/namespaces/${ns.id}/revoke`, {}))
          }
        >
          Revoke semantic namespace
        </button>
      )}
      {canApprove && (
        <details>
          <summary>Delete this namespace</summary>
          <p>
            Deletes its encrypted catalog and abandoned staging content.
            Finalized datasets retain their own policy lifecycle.
          </p>
          <Field
            name="delete-hash"
            label="Exact namespace hash to delete"
            value={deleteHash}
            onChange={(event) => setDeleteHash(event.target.value)}
          />
          <Field
            name="delete-ref"
            label="Namespace deletion reference"
            value={deleteRef}
            onChange={(event) => setDeleteRef(event.target.value)}
          />
          <button
            disabled={
              busy || deleteHash !== ns.specification_hash || !deleteRef
            }
            onClick={() =>
              act(() =>
                json(
                  api,
                  `${base}/namespaces/${ns.id}`,
                  { approval_hash: deleteHash, approval_ref: deleteRef },
                  "DELETE",
                ),
              )
            }
          >
            Delete reviewed namespace
          </button>
        </details>
      )}
    </section>
  );
}

function CuratedCatalog({
  api,
  base,
  ns,
  canApprove,
  busy,
  act,
}: {
  api: API;
  base: string;
  ns: Namespace;
  canApprove: boolean;
  busy: boolean;
  act: (action: () => Promise<unknown>) => Promise<void>;
}) {
  const [entries, setEntries] = useState<any[]>([]);
  const [selected, setSelected] = useState("");
  const [purpose, setPurpose] = useState("");
  const [content, setContent] = useState<any>(null);
  const [error, setError] = useState("");
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    api(`${base}/namespaces/${ns.id}/entries`)
      .then((value) => {
        if (live.current) setEntries(value);
      })
      .catch((error) => {
        if (live.current) setError(explain(error));
      });
    return () => {
      live.current = false;
    };
  }, [api, base, ns.id, ns.entry_count]);
  return (
    <details>
      <summary>Curated catalog metadata and authorized review</summary>
      {error && <p role="status">{error}</p>}
      <ul>
        {entries.map((entry) => (
          <li key={entry.id}>
            {entry.id.slice(0, 8)} · {entry.expired ? "Expired" : "Retained"} ·
            expires {new Date(entry.expires_at).toLocaleString()}
          </li>
        ))}
      </ul>
      {!entries.length && <p>No curated answers.</p>}
      {canApprove && (
        <>
          <Select
            name="review-entry"
            label="Catalog entry to review"
            value={selected}
            disabled={busy}
            onChange={(event) => {
              setContent(null);
              setSelected(event.target.value);
            }}
          >
            <option value="">Choose an encrypted answer</option>
            {entries.map((entry) => (
              <option key={entry.id} value={entry.id}>
                {entry.id}
              </option>
            ))}
          </Select>
          <Field
            name="content-purpose"
            label="Catalog content review purpose"
            value={purpose}
            disabled={busy}
            onChange={(event) => setPurpose(event.target.value)}
          />
          <button
            disabled={busy || !selected || !purpose}
            onClick={() =>
              act(async () => {
                const value = await json(
                  api,
                  `${base}/namespaces/${ns.id}/entries/${selected}/content`,
                  { purpose },
                );
                if (live.current) setContent(value);
              })
            }
          >
            Read purpose-authorized catalog content
          </button>
          {content && (
            <>
              <button onClick={() => setContent(null)}>
                Clear reviewed catalog content
              </button>
              <pre>{JSON.stringify(content, null, 2)}</pre>
            </>
          )}
        </>
      )}
    </details>
  );
}

function QualificationForm({
  ns,
  datasets,
  busy,
  submit,
}: {
  ns: Namespace;
  datasets: any[];
  busy: boolean;
  submit: (body: unknown) => void;
}) {
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        const data = values(event.currentTarget);
        submit({
          dataset_id: data.dataset,
          max_wall_seconds: Number(data.wall),
          specification: {
            rule: ns.specification.rule,
            thresholds: String(data.thresholds).split(",").map(Number),
            maximum_false_positive_group_upper95: Number(data.falsePositive),
            minimum_useful_hit_group_ratio: Number(data.useful),
            maximum_utility_regression: Number(data.regression),
            minimum_negative_groups: Number(data.negative),
            minimum_positive_groups: Number(data.positive),
          },
        });
      }}
    >
      <h4>3. Freeze quality gates</h4>
      <Select name="dataset" label="Qualification dataset" required>
        <option value="">Choose an independent labeled dataset</option>
        {datasets
          .filter(
            (row) =>
              row.workflow === ns.specification.workflow &&
              row.provenance.policy_id === ns.specification.policy_id,
          )
          .map((row) => (
            <option key={row.id} value={row.id}>
              {row.provenance.provenance_ref} · {row.id.slice(0, 8)}
            </option>
          ))}
      </Select>
      <div className="form-grid">
        <Field
          name="thresholds"
          label="Validation-only threshold grid"
          defaultValue="0.5,0.99"
          required
        />
        <Field
          name="falsePositive"
          label="Maximum false-hit group upper 95% bound"
          type="number"
          step="0.001"
          min={0}
          max={1}
          defaultValue={0.1}
          required
        />
        <Field
          name="useful"
          label="Minimum useful-hit group ratio"
          type="number"
          step="0.01"
          min={0.01}
          max={1}
          defaultValue={0.1}
          required
        />
        <Field
          name="regression"
          label="Maximum task-utility regression"
          type="number"
          step="0.01"
          min={0}
          max={1}
          defaultValue={0}
          required
        />
        <Field
          name="negative"
          label="Minimum negative control groups"
          type="number"
          min={3}
          max={512}
          defaultValue={30}
          required
        />
        <Field
          name="positive"
          label="Minimum positive control groups"
          type="number"
          min={3}
          max={512}
          defaultValue={10}
          required
        />
        <Field
          name="wall"
          label="Maximum active local processing seconds"
          type="number"
          min={1}
          max={120}
          defaultValue={120}
          required
        />
      </div>
      <button disabled={busy} type="submit">
        Prepare frozen qualification
      </button>
    </form>
  );
}

function QualificationReview({
  row,
  api,
  base,
  canApprove,
  busy,
  act,
}: {
  row: Qualification;
  api: API;
  base: string;
  canApprove: boolean;
  busy: boolean;
  act: (action: () => Promise<unknown>) => Promise<void>;
}) {
  const [approvalRef, setApprovalRef] = useState("");
  const [activation, setActivation] = useState<any>(null);
  const live = useRef(true);
  useEffect(
    () => () => {
      live.current = false;
    },
    [],
  );
  const route = `${base}/qualifications/${row.id}`;
  const report = row.report as any;
  const confirmation = report.confirmation;
  return (
    <section aria-label={`Cache qualification ${row.id.slice(0, 8)}`}>
      <h5>
        {row.id.slice(0, 8)} · {row.state}
      </h5>
      <p>
        Approval hash <code>{row.approval_hash}</code>
      </p>
      <p>
        Selected threshold: {row.selected_threshold ?? "Not selected"}. Complete
        task cost and realized savings: unknown.
      </p>
      <p>
        Accounted local processing:{" "}
        {row.accounted_active_processing_us / 1000000}s; uncertain:{" "}
        {row.uncertain_active_processing_us / 1000000}s; held:{" "}
        {row.held_active_processing_us / 1000000}s.
      </p>
      {confirmation && (
        <dl>
          <dt>Useful hit groups</dt>
          <dd>
            {confirmation.useful_hit_groups} / {confirmation.declared_groups}
          </dd>
          <dt>False-hit groups / negative controls</dt>
          <dd>
            {confirmation.unsafe_hit_groups} /{" "}
            {confirmation.negative_control_groups}
          </dd>
          <dt>False-hit group upper 95% bound</dt>
          <dd>{confirmation.false_positive_group_upper95 ?? "Unknown"}</dd>
          <dt>Group-weighted task utility change</dt>
          <dd>{confirmation.group_weighted_utility_delta ?? "Unknown"}</dd>
        </dl>
      )}
      <details>
        <summary>Frozen gates and full aggregate evidence</summary>
        <pre>
          {JSON.stringify(
            { specification: row.specification, report: row.report },
            null,
            2,
          )}
        </pre>
      </details>
      {canApprove && (
        <>
          <Field
            name="approval-ref"
            label={`Cache approval reference ${row.id.slice(0, 8)}`}
            value={approvalRef}
            onChange={(event) => setApprovalRef(event.target.value)}
          />
          {row.state === "PENDING" && (
            <button
              disabled={busy || !approvalRef}
              onClick={() =>
                act(() =>
                  json(api, route + "/approve", {
                    approval_hash: row.approval_hash,
                    approval_ref: approvalRef,
                  }),
                )
              }
            >
              Approve local cache qualification
            </button>
          )}
          {row.state === "PASSED" && (
            <button
              disabled={busy}
              onClick={() =>
                act(async () => {
                  const value = await api(route + "/activation");
                  if (live.current) setActivation(value);
                })
              }
            >
              Review activation eligibility
            </button>
          )}
          {activation && (
            <>
              <p>
                Activation hash <code>{activation.activation_hash}</code>
              </p>
              <p>
                {activation.eligible
                  ? "Passing evidence is currently eligible."
                  : explain(new Error(activation.ineligibility_reason))}
              </p>
              <button
                disabled={busy || !approvalRef || !activation.eligible}
                onClick={() =>
                  act(() =>
                    json(api, route + "/activate", {
                      approval_hash: activation.activation_hash,
                      approval_ref: approvalRef,
                    }),
                  )
                }
              >
                Activate reviewed semantic cache
              </button>
            </>
          )}
          {[
            "PENDING",
            "QUEUED",
            "CALIBRATING",
            "CALIBRATED",
            "CONFIRMING",
          ].includes(row.state) && (
            <button
              disabled={busy}
              onClick={() => act(() => json(api, route + "/cancel", {}))}
            >
              Cancel cache qualification
            </button>
          )}
        </>
      )}
    </section>
  );
}
