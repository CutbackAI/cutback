import React, { useRef, useState } from "react";
import {
  type API,
  type Data,
  Check,
  Editor,
  Field,
  Form,
  Select,
  json,
  explain,
} from "./ui";

export function Imports({
  api,
  base,
  policies,
  rows,
  analyst,
  reload,
}: {
  api: API;
  base: string;
  policies: Data[];
  rows: Data;
  analyst: boolean;
  reload: () => void;
}) {
  const [preview, setPreview] = useState<Data | null>(null);
  const [examples, setExamples] = useState<Data | null>(null);
  const [imported, setImported] = useState("");
  const [resumeError, setResumeError] = useState("");
  const version = useRef(0);
  const availablePolicies = policies.filter(
    (p) => p.enabled && Date.parse(p.policy?.expires_at) > Date.now(),
  );
  return (
    <section aria-label="Qualified export imports">
      <h2>Existing exports</h2>
      <p>
        Bring supported trace or evaluation exports into an unreviewed dataset.
        Imported outputs remain historical references; source grades are
        externally asserted. Importing makes no provider calls.
      </p>
      {!!rows?.items?.length && (
        <ul>
          {rows.items.map((r: Data) => (
            <li key={r.id}>
              {r.profile} · {r.accepted} compatible / {r.rejected} rejected ·{" "}
              {r.state}
              {analyst && (
                <button
                  onClick={async () => {
                    const current = ++version.current;
                    setResumeError("");
                    setPreview(null);
                    setExamples(null);
                    setImported("");
                    try {
                      const value = await api(base + "/imports/" + r.id);
                      if (current !== version.current) return;
                      setPreview(value);
                      setImported(value.dataset_id || "");
                    } catch (e) {
                      if (current === version.current)
                        setResumeError(explain(e));
                    }
                  }}
                >
                  Resume import {r.id.slice(0, 8)}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {resumeError && <p role="alert">{resumeError}</p>}
      {rows?.next_cursor && (
        <p>
          More previews exist; use the cursor-paginated imports API for older
          records.
        </p>
      )}
      {analyst && (
        <Editor title="Import a qualified export" open={!!preview}>
          <Form
            title="Upload an encrypted import preview"
            submit="Upload for encrypted preview"
            description="Choose the exact supported format. The file stays local until you submit; the preview uploads permitted projected cases to encrypted storage without creating a dataset."
            onSubmit={async (data) => {
              const current = ++version.current;
              const file = data.file;
              if (!(file instanceof File) || !file.size || file.size > 1048576)
                throw new Error("IMPORT_FILE_UNSAFE_OR_TOO_LARGE");
              const raw = await file.arrayBuffer();
              const source = new TextDecoder("utf-8", {
                fatal: true,
                ignoreBOM: true,
              }).decode(raw);
              const sourceHash = Array.from(
                new Uint8Array(await crypto.subtle.digest("SHA-256", raw)),
              )
                .map((b) => b.toString(16).padStart(2, "0"))
                .join("");
              const body = {
                profile: String(data.profile),
                source_text: source,
                source_sha256: sourceHash,
                policy_id: String(data.policy),
                workflow: String(data.workflow),
                environment: String(data.environment),
                provenance_ref: String(data.provenance),
              };
              if (
                new TextEncoder().encode(JSON.stringify(body)).length > 1048576
              )
                throw new Error(
                  "The encoded upload exceeds the collector's 1 MiB request limit. Split the export into smaller authorized files.",
                );
              const value = await json(api, base + "/imports/preview", body);
              if (current !== version.current)
                return "Preview saved; another preview is selected.";
              setPreview(value);
              setExamples(null);
              setImported("");
              reload();
              return `Encrypted preview: ${value.accepted} compatible rows, ${value.rejected} rejections. Dataset not created.`;
            }}
          >
            <Select label="Export format" name="profile" required>
              <option value="cutback-jsonl.v1">Cutback case JSONL v1</option>
              <option value="openinference-otlp-json.v1">
                OpenInference OTLP JSON projection v1
              </option>
              <option value="promptfoo-0.125.1-results.v3">
                Promptfoo 0.125.1 result export v3
              </option>
              <option value="bedrock-automated-model-jsonl.v1">
                Amazon Bedrock automated text-model JSONL v1
              </option>
              <option value="azure-ai-evaluation-1.18.10-results.v1">
                Azure AI Evaluation 1.18.10 row results
              </option>
              <option value="vertexai-1.153.1-metrics-jsonl.v1">
                Vertex AI 1.153.1 pointwise table JSONL
              </option>
            </Select>
            <Field
              type="file"
              label="Qualified export file"
              name="file"
              required
              accept=".json,.jsonl,.ndjson"
            />
            {availablePolicies.length ? (
              <Select label="Import rights policy" name="policy" required>
                <option value="">Select an active policy</option>
                {availablePolicies.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.policy?.approval_ref || p.id}
                  </option>
                ))}
              </Select>
            ) : (
              <Field label="Import rights policy UUID" name="policy" required />
            )}
            <Field
              label="Import workflow"
              name="workflow"
              required
              maxLength={200}
            />
            <Field
              label="Import environment"
              name="environment"
              defaultValue="staging"
              required
              maxLength={100}
            />
            <Field
              label="Import provenance reference"
              name="provenance"
              required
              maxLength={200}
            />
            <Check
              name="upload_authorized"
              label="I authorize uploading this export under the selected rights policy."
              required
            />
          </Form>
          {preview && (
            <section aria-label="Frozen import mapping">
              <h3>Review the frozen mapping</h3>
              <p>
                Profile: {preview.profile}. Source hash:{" "}
                <code>{preview.source_sha256}</code>
              </p>
              <dl>
                {Object.entries(preview.mapping || {}).map(
                  ([field, source]) => (
                    <React.Fragment key={field}>
                      <dt>{field.replaceAll("_", " ")}</dt>
                      <dd>{String(source)}</dd>
                    </React.Fragment>
                  ),
                )}
              </dl>
              <p>
                {preview.accepted} accepted · {preview.rejected} rejected ·{" "}
                {preview.literal_expectations} declared expectations ·{" "}
                {preview.historical_references} historical outputs ·{" "}
                {preview.source_asserted_checks} source checks.
              </p>
              <p>
                All imported labels remain unreviewed. Script assertions,
                config, credentials and unsupported fields are projected out; no
                source code is executed.
              </p>
              {!!preview.rejections?.length && (
                <ul>
                  {preview.rejections.map((r: Data) => (
                    <li key={r.row}>
                      Row {r.row}: {r.code.replaceAll("_", " ").toLowerCase()}
                    </li>
                  ))}
                </ul>
              )}
              {preview.rejections_truncated && (
                <p>Only the first 100 rejection locations are shown.</p>
              )}
              <Form
                key={"read-" + preview.id}
                title="Read permitted import examples"
                submit="Read mapping examples"
                onSubmit={async (data) => {
                  const current = version.current;
                  const result = await json(
                    api,
                    base + "/imports/" + preview.id + "/content",
                    { purpose: String(data.purpose) },
                  );
                  if (current === version.current) setExamples(result);
                  return "Purpose recorded; imported content is not independent gold.";
                }}
              >
                <Field
                  label="Import content read purpose"
                  name="purpose"
                  required
                  maxLength={200}
                />
              </Form>
              {examples && (
                <div className="import-examples">
                  <p>
                    Permitted mapping examples, at most 25. Text is displayed as
                    data.
                  </p>
                  {examples.cases.map((c: Data) => (
                    <pre tabIndex={0} key={c.id}>
                      {JSON.stringify(
                        {
                          input: c.input,
                          declared_expectation_unreviewed: c.gold,
                          historical_reference: c.reference,
                        },
                        null,
                        2,
                      )}
                    </pre>
                  ))}
                  {examples.cases_truncated && (
                    <p>Example display truncated.</p>
                  )}
                  {!!examples.source_asserted_checks?.length && (
                    <section aria-label="Source-reported metric values">
                      <h4>Source-reported metrics</h4>
                      <p>
                        These are external assertions. Original values are kept;
                        no common scale, pass threshold or independent task
                        success is inferred. Missing, error and skipped results
                        stay separate. Metric names appear only in this
                        authorized content view.
                      </p>
                      {examples.source_asserted_checks.map(
                        (row: Data, index: number) => (
                          <dl key={`${row.case_id}-${row.metric_id || index}`}>
                            <dt>Source metric</dt>
                            <dd>{String(row.metric_name || row.evaluator)}</dd>
                            <dt>Source case</dt>
                            <dd>{String(row.case_id)}</dd>
                            {row.source_model_identifier != null && (
                              <>
                                <dt>Source model identifier</dt>
                                <dd>{String(row.source_model_identifier)}</dd>
                              </>
                            )}
                            <dt>Reported state</dt>
                            <dd>{String(row.outcome)}</dd>
                            <dt>Original value</dt>
                            <dd>
                              {row.score != null
                                ? String(row.score)
                                : row.boolean_value != null
                                  ? String(row.boolean_value)
                                  : "unknown"}
                            </dd>
                          </dl>
                        ),
                      )}
                      {examples.checks_truncated && (
                        <p>
                          Only the first 25 source metric records are shown.
                        </p>
                      )}
                    </section>
                  )}
                  <button onClick={() => setExamples(null)}>
                    Hide import examples
                  </button>
                </div>
              )}
              {!imported ? (
                <Form
                  key={"commit-" + preview.id}
                  title="Create the unreviewed dataset"
                  submit="Import reviewed mapping"
                  onSubmit={async (data) => {
                    const current = version.current;
                    const value = await json(
                      api,
                      base + "/imports/" + preview.id + "/commit",
                      {
                        preview_hash: preview.preview_hash,
                        mapping_reviewed: data.mapping === "on",
                        rights_confirmed: data.rights === "on",
                        accept_rejections: data.rejections === "on",
                      },
                    );
                    if (current === version.current) {
                      setImported(value.id);
                      setExamples(null);
                    }
                    reload();
                    return "Unreviewed immutable dataset created. No inference was run.";
                  }}
                >
                  <Check
                    label="I reviewed this exact source hash and mapping."
                    name="mapping"
                    required
                  />
                  <Check
                    label="These projected cases are authorized for this policy and retention."
                    name="rights"
                    required
                  />
                  {preview.rejected > 0 && (
                    <Check
                      label="I explicitly accept importing only compatible rows and preserving the rejection count."
                      name="rejections"
                      required
                    />
                  )}
                </Form>
              ) : (
                <p role="status">
                  Unreviewed dataset created: <code>{imported}</code>. Human
                  review is required before claiming independent labels.
                </p>
              )}
            </section>
          )}
        </Editor>
      )}
    </section>
  );
}
