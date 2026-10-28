import React, { useEffect, useRef, useState } from "react";
import {
  API,
  Data,
  Form,
  Field,
  Select,
  Check,
  Editor,
  json,
  explain,
} from "./ui";

const initialDocument = JSON.stringify(
  {
    kind: "text",
    text: "Classify {{input}}. Return a single label.",
    variables: { input: "string" },
    dependencies: {},
  },
  null,
  2,
);

function JSONField({
  label,
  name,
  initial = "{}",
}: {
  label: string;
  name: string;
  initial?: string;
}) {
  return (
    <label className="field">
      {label}
      <textarea
        name={name}
        rows={5}
        maxLength={128000}
        defaultValue={initial}
        required
        spellCheck={false}
      />
    </label>
  );
}

export function Prompts({
  api,
  base,
  data,
  analyst,
  editor,
  admin,
}: {
  api: API;
  base: string;
  data: Data;
  analyst: boolean;
  editor: boolean;
  admin: boolean;
}) {
  const [records, setRecords] = useState<Data[]>(data.prompts.items);
  const [cursor, setCursor] = useState<string | null>(data.prompts.next_cursor);
  const [selected, setSelected] = useState<Data | null>(null);
  const [versions, setVersions] = useState<Data[]>([]);
  const [versionCursor, setVersionCursor] = useState<number | null>(null);
  const [active, setActive] = useState<Data | null>(null);
  const [aliases, setAliases] = useState<Data[]>([]);
  const [aliasName, setAliasName] = useState("staging");
  const [aliasCursor, setAliasCursor] = useState<string | null>(null);
  const [impact, setImpact] = useState<Data | null>(null);
  const [history, setHistory] = useState<Data | null>(null);
  const [content, setContent] = useState<Data | null>(null);
  const [preview, setPreview] = useState<Data | null>(null);
  const [diff, setDiff] = useState<Data | null>(null);
  const [grants, setGrants] = useState<Data[]>(data.grants?.items || []);
  const [grantCursor, setGrantCursor] = useState<string | null>(
    data.grants?.next_cursor || null,
  );
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  const versionRequest = useRef(crypto.randomUUID());
  const [environment, setEnvironment] = useState("staging");
  const pbase = selected ? base + "/prompts/" + selected.id : "";
  const expectedAlias = aliases.find(
    (a) => a.name === aliasName && a.environment === environment,
  );
  const context = (purpose: FormDataEntryValue) => ({
    purpose,
    environment,
    workflow: selected!.workflow,
  });
  useEffect(() => {
    setRecords(data.prompts.items);
    setCursor(data.prompts.next_cursor);
  }, [data.prompts]);
  useEffect(() => {
    setGrants(data.grants?.items || []);
    setGrantCursor(data.grants?.next_cursor || null);
  }, [data.grants]);
  async function retainPrivate(
    path: string,
    body: unknown,
    apply: (value: Data) => void,
  ) {
    const id = generation.current;
    const result = await json(api, path, body);
    if (id !== generation.current) return false;
    apply(result);
    return true;
  }

  async function action(run: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setNotice("");
    try {
      await run();
    } catch (e) {
      setNotice(explain(e));
    } finally {
      setBusy(false);
    }
  }
  async function refresh(prompt: Data) {
    const id = ++generation.current;
    const [v, a, i] = await Promise.all([
      api(base + "/prompts/" + prompt.id + "/versions"),
      api(base + "/prompts/" + prompt.id + "/aliases"),
      api(base + "/prompts/" + prompt.id + "/impact"),
    ]);
    if (id !== generation.current) return;
    setVersions(v.items);
    setVersionCursor(v.next_cursor);
    setActive(v.items[0] || null);
    setAliases(a.items);
    setAliasCursor(a.next_cursor);
    setImpact(i);
    setContent(null);
    setPreview(null);
    setDiff(null);
    setHistory(null);
  }
  async function open(prompt: Data) {
    setAliasName("staging");
    setSelected(prompt);
    setVersions([]);
    setActive(null);
    setContent(null);
    setPreview(null);
    setDiff(null);
    setHistory(null);
    setImpact(null);
    await refresh(prompt);
  }
  const policyOptions = (data.policies || [])
    .filter(
      (p: Data) =>
        p.enabled &&
        p.policy?.environments?.includes(environment) &&
        (!selected || p.policy?.workflows?.includes(selected.workflow)) &&
        Date.parse(p.policy?.expires_at) > Date.now(),
    )
    .map((p: Data) => (
      <option key={p.id} value={p.id}>
        {p.policy?.approval_ref || p.approval_ref} · {p.id}
      </option>
    ));
  return (
    <section aria-label="Prompt registry workspace">
      <p>
        Version a prompt, compare it on a frozen dataset, then approve an exact
        alias change. Model execution requires a separate budget approval and
        configured worker.
      </p>
      <p className="hint">
        Prompt content stays encrypted. Opening a version requires a purpose and
        current rights to every component. Consumer reports have unknown
        coverage.
      </p>
      {notice && <p role="alert">{notice}</p>}
      <h2>Manage prompts</h2>
      {analyst && (
        <Editor title="Create a prompt">
          <Form
            title="Prompt identity"
            submit="Create prompt"
            onSubmit={async (f) => {
              const result = await json(api, base + "/prompts", {
                key: f.key,
                workflow: f.workflow,
              });
              setRecords((old) =>
                old.some((r) => r.id === result.id) ? old : [...old, result],
              );
              await open(result);
              return "Prompt created. Add its first immutable version.";
            }}
          >
            <Field
              label="Prompt key"
              name="key"
              required
              pattern="[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}"
            />
            <Field
              label="Prompt workflow"
              name="workflow"
              required
              defaultValue="classify"
            />
          </Form>
        </Editor>
      )}
      <h2>Registry</h2>
      {records.length === 0 ? (
        <p className="empty">
          No prompt versions yet. Create a prompt and add an authorized
          template.
        </p>
      ) : (
        <div className="table-scroll" tabIndex={0}>
          <table aria-label="Registered prompts">
            <thead>
              <tr>
                <th>Prompt</th>
                <th>Workflow</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {records.map((r) => (
                <tr key={r.id}>
                  <td>{r.key}</td>
                  <td>{r.workflow}</td>
                  <td>
                    <button
                      disabled={busy}
                      onClick={() => action(() => open(r))}
                    >
                      Open {r.key}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {cursor && (
        <button
          disabled={busy}
          onClick={() =>
            action(async () => {
              const r = await api(
                base + "/prompts?after=" + encodeURIComponent(cursor),
              );
              setRecords((old) => [...old, ...r.items]);
              setCursor(r.next_cursor);
            })
          }
        >
          More prompts
        </button>
      )}
      {selected && (
        <div key={selected.id}>
          <h2>{selected.key}</h2>
          <button
            disabled={busy}
            onClick={() =>
              action(async () => {
                await refresh(selected);
                versionRequest.current = crypto.randomUUID();
              })
            }
          >
            Refresh prompt metadata
          </button>
          <label className="field">
            Consumer environment
            <input
              value={environment}
              onChange={(e) => {
                setEnvironment(e.target.value);
                generation.current++;
                setContent(null);
                setPreview(null);
                setDiff(null);
              }}
              maxLength={100}
              required
            />
          </label>
          <h3>Immutable versions</h3>
          {versions.length === 0 && (
            <p>No versions. Author the first version below.</p>
          )}
          <div className="table-scroll" tabIndex={0}>
            <table aria-label="Prompt versions">
              <thead>
                <tr>
                  <th>Version</th>
                  <th>Source</th>
                  <th>Content reference</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {versions.map((v) => (
                  <tr key={v.id}>
                    <td>
                      v{v.ordinal}
                      <br />
                      <code>{v.id}</code>
                    </td>
                    <td>{v.provenance_ref}</td>
                    <td>
                      {v.content_reference_unexpired
                        ? "Unexpired; rights checked on read"
                        : "Expired or unavailable"}
                    </td>
                    <td>
                      <button
                        disabled={busy}
                        aria-pressed={active?.id === v.id}
                        onClick={() => {
                          generation.current++;
                          setActive(v);
                          setContent(null);
                          setPreview(null);
                        }}
                      >
                        Select v{v.ordinal}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {versionCursor && (
            <button
              disabled={busy}
              onClick={() =>
                action(async () => {
                  const r = await api(
                    pbase + "/versions?before=" + versionCursor,
                  );
                  setVersions((old) => [...old, ...r.items]);
                  setVersionCursor(r.next_cursor);
                })
              }
            >
              Older prompt versions
            </button>
          )}
          {analyst && (
            <Editor title="Author a new version">
              <Form
                title="Immutable prompt version"
                submit="Save encrypted version"
                description="Templates support declared {{variables}} and pinned {{>components}}. Text and chat documents are supported. Python, Jinja, file and network includes are rejected."
                onSubmit={async (f) => {
                  const r = await json(api, pbase + "/versions", {
                    version_id: versionRequest.current,
                    expected_latest_ordinal: versions[0]?.ordinal || 0,
                    policy_id: f.policy,
                    environment,
                    provenance_ref: f.source,
                    document: JSON.parse(String(f.document)),
                  });
                  versionRequest.current = crypto.randomUUID();
                  await refresh(selected);
                  setActive(r);
                  return "Version frozen. No model calls made.";
                }}
              >
                {policyOptions.length ? (
                  <Select label="Prompt content policy" name="policy" required>
                    {policyOptions}
                  </Select>
                ) : (
                  <Field
                    label="Prompt content policy ID"
                    name="policy"
                    required
                  />
                )}
                <Field
                  label="Version source reference"
                  name="source"
                  defaultValue="reviewed-template-v1"
                  required
                />
                <JSONField
                  label="Prompt document (JSON)"
                  name="document"
                  initial={initialDocument}
                />
                <Check
                  name="authorized"
                  label="This policy authorizes storing this prompt, schema and its components."
                  required
                />
              </Form>
            </Editor>
          )}
          {active && (
            <>
              <h3>Selected version: v{active.ordinal}</h3>
              <p>
                <code>{active.document_hash}</code>
              </p>
              {analyst && (
                <Editor title="Read or preview authorized content">
                  <Form
                    title="Purpose-bound prompt read"
                    submit="Read selected prompt"
                    onSubmit={async (f) => {
                      const retained = await retainPrivate(
                        pbase + "/versions/" + active.id + "/content",
                        context(f.purpose),
                        setContent,
                      );
                      if (!retained)
                        return "View changed; the earlier content response was discarded.";
                      setPreview(null);
                      return "Authorized graph loaded into this view only.";
                    }}
                  >
                    <Field
                      label="Content read purpose"
                      name="purpose"
                      required
                      defaultValue="inspect-prompt-change"
                    />
                  </Form>
                  {content && (
                    <>
                      <button
                        onClick={() => {
                          generation.current++;
                          setContent(null);
                          setPreview(null);
                          setDiff(null);
                        }}
                      >
                        Clear prompt content
                      </button>
                      <pre tabIndex={0}>
                        {JSON.stringify(content.documents, null, 2)}
                      </pre>
                    </>
                  )}
                  <Form
                    title="Render preview"
                    submit="Render without inference"
                    onSubmit={async (f) => {
                      const retained = await retainPrivate(
                        pbase + "/versions/" + active.id + "/preview",
                        {
                          ...context(f.purpose),
                          variables: JSON.parse(String(f.variables)),
                          values_authorized: f.authorized === "on",
                        },
                        setPreview,
                      );
                      if (!retained)
                        return "View changed; the earlier preview was discarded.";
                      return "Rendered only; no provider calls or retained variable values.";
                    }}
                  >
                    <Field
                      label="Preview purpose"
                      name="purpose"
                      required
                      defaultValue="review-template-rendering"
                    />
                    <JSONField
                      label="Preview variable values (JSON)"
                      name="variables"
                      initial={'{"input":"Independent synthetic case"}'}
                    />
                    <Check
                      label="I authorize sending these preview values to this collector."
                      name="authorized"
                      required
                    />
                  </Form>
                  {preview && (
                    <pre tabIndex={0}>
                      {JSON.stringify(preview.rendered, null, 2)}
                    </pre>
                  )}
                </Editor>
              )}
              {analyst && versions.length > 1 && (
                <Editor title="Compare prompt versions">
                  <Form
                    title="Purpose-bound version diff"
                    submit="Compare content"
                    onSubmit={async (f) => {
                      const retained = await retainPrivate(
                        pbase + "/diff",
                        {
                          ...context(f.purpose),
                          left_version_id: f.left,
                          right_version_id: active.id,
                        },
                        setDiff,
                      );
                      if (!retained)
                        return "View changed; the earlier diff was discarded.";
                      return "Diff loaded with current content rights.";
                    }}
                  >
                    <Select
                      label="Compare selected version against"
                      name="left"
                    >
                      {versions
                        .filter((v) => v.id !== active.id)
                        .map((v) => (
                          <option key={v.id} value={v.id}>
                            v{v.ordinal}
                          </option>
                        ))}
                    </Select>
                    <Field
                      label="Diff purpose"
                      name="purpose"
                      required
                      defaultValue="review-prompt-diff"
                    />
                  </Form>
                  {diff && (
                    <>
                      <button onClick={() => setDiff(null)}>
                        Clear prompt diff
                      </button>
                      <pre tabIndex={0}>
                        {typeof diff.diff === "string"
                          ? diff.diff
                          : JSON.stringify(diff, null, 2)}
                      </pre>
                    </>
                  )}
                </Editor>
              )}
              {analyst && (
                <Playground
                  api={api}
                  base={base}
                  prompt={selected}
                  versions={versions}
                  environment={environment}
                  datasets={data.datasets || []}
                />
              )}
            </>
          )}
          <h3>Environment aliases</h3>
          <p className="hint">
            Promotion changes a registry pointer. Applications resolve it
            explicitly; this does not deploy code or prove application quality.
          </p>
          {aliases.length === 0 && <p>No aliases.</p>}
          {aliases.map((a) => (
            <div key={a.id}>
              <p>
                <strong>{a.name}</strong> · revision {a.revision} · version{" "}
                <code>{a.version_id}</code> · environment{" "}
                <code>{a.environment || a.environment_id}</code>
              </p>
              <button
                disabled={busy}
                onClick={() =>
                  action(async () => {
                    setHistory({
                      alias: a,
                      ...(await api(
                        base + "/prompt-aliases/" + a.id + "/history",
                      )),
                    });
                  })
                }
              >
                History for {a.name}
              </button>
            </div>
          ))}
          {aliasCursor && (
            <button
              disabled={busy}
              onClick={() =>
                action(async () => {
                  const r = await api(
                    pbase + "/aliases?after=" + encodeURIComponent(aliasCursor),
                  );
                  setAliases((old) => [...old, ...r.items]);
                  setAliasCursor(r.next_cursor);
                })
              }
            >
              More aliases
            </button>
          )}
          {editor && active && (
            <Editor title="Promote selected version">
              <Form
                title="Reviewed alias promotion"
                submit="Promote exact version"
                description="Review the loaded alias head and selected version before promotion. The request binds that exact head; a concurrent change is rejected. Refresh metadata before retrying a stale change."
                onSubmit={async (f) => {
                  await json(api, pbase + "/aliases", {
                    version_id: active.id,
                    environment,
                    alias: f.alias,
                    expected_revision: Number(f.revision),
                    expected_version_id: f.previous || null,
                    approval_ref: f.approval,
                    quality_unverified_acknowledged: f.unverified === "on",
                    evidence_run_id: f.run || null,
                    evidence_model_key: f.model || null,
                  });
                  await refresh(selected);
                  return "Alias changed with an immutable receipt. No deployment performed.";
                }}
              >
                <Field
                  label="Alias name"
                  name="alias"
                  value={aliasName}
                  onChange={(e) => setAliasName(e.target.value)}
                  required
                />
                <Field
                  label="Expected alias revision"
                  name="revision"
                  type="number"
                  min="0"
                  value={expectedAlias?.revision || 0}
                  readOnly
                  required
                />
                <Field
                  label="Expected current version ID (empty for new alias)"
                  name="previous"
                  value={expectedAlias?.version_id || ""}
                  readOnly
                />
                <Field
                  label="Alias approval reference"
                  name="approval"
                  required
                />
                <Field label="Confirmation run ID (optional)" name="run" />
                <Field label="Confirmation model key (with run)" name="model" />
                <Check
                  label="I acknowledge that this change has no verified quality claim unless the linked exact-version evidence qualifies."
                  name="unverified"
                />
              </Form>
            </Editor>
          )}
          {history && (
            <section aria-label="Prompt alias history">
              <h3>{history.alias.name} history</h3>
              {history.items.map((r: Data) => (
                <div key={r.id}>
                  <p>
                    Revision {r.revision}: {r.quality_status} · {r.approval_ref}
                  </p>
                  <p>
                    <code>{r.from_version_id || "No previous version"}</code> →{" "}
                    <code>{r.to_version_id}</code>
                  </p>
                  {editor &&
                    r.revision === history.alias.revision &&
                    r.from_version_id && (
                      <Form
                        title={"Rollback revision " + r.revision}
                        submit="Restore exact previous version"
                        onSubmit={async (f) => {
                          await json(
                            api,
                            base +
                              "/prompt-aliases/" +
                              history.alias.id +
                              "/rollback",
                            {
                              receipt_id: r.id,
                              expected_revision: r.revision,
                              approval_ref: f.approval,
                            },
                          );
                          await refresh(selected);
                          return "Previous pointer restored with a new receipt.";
                        }}
                      >
                        <Field
                          label="Rollback approval reference"
                          name="approval"
                          required
                        />
                      </Form>
                    )}
                </div>
              ))}
              {history.next_cursor && (
                <button
                  onClick={() =>
                    action(async () => {
                      const next = await api(
                        base +
                          "/prompt-aliases/" +
                          history.alias.id +
                          "/history?before_revision=" +
                          history.next_cursor,
                      );
                      setHistory({
                        ...history,
                        items: [...history.items, ...next.items],
                        next_cursor: next.next_cursor,
                      });
                    })
                  }
                >
                  Older alias receipts
                </button>
              )}
            </section>
          )}
          {impact && (
            <section aria-label="Prompt impact">
              <h3>Known consumers and impact</h3>
              <p>
                {impact.registered_dependency_edges} pinned component references
                · {impact.registered_experiment_uses} experiment uses
              </p>
              <p>
                Consumer coverage: unknown. Reports below are externally
                asserted, retained observations; absent reports do not prove a
                release is unused.
              </p>
              {impact.items.length === 0 ? (
                <p>No retained consumer reports.</p>
              ) : (
                <div className="table-scroll" tabIndex={0}>
                  <table aria-label="Reported prompt consumers">
                    <thead>
                      <tr>
                        <th>Consumer</th>
                        <th>Release</th>
                        <th>Version</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {impact.items.map((r: Data) => (
                        <tr key={r.observation_id}>
                          <td>{r.consumer}</td>
                          <td>{r.release}</td>
                          <td>
                            <code>{r.version_id}</code>
                          </td>
                          <td>{r.status}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {impact.next_cursor && (
                <button
                  disabled={busy}
                  onClick={() =>
                    action(async () => {
                      const next = await api(
                        pbase +
                          "/impact?after=" +
                          encodeURIComponent(impact.next_cursor),
                      );
                      setImpact({
                        ...next,
                        items: [...impact.items, ...next.items],
                      });
                    })
                  }
                >
                  More consumer reports
                </button>
              )}
            </section>
          )}
          {admin && (
            <Editor title="Grant scoped prompt reads">
              <Form
                title="Prompt read authorization"
                submit="Grant prompt read"
                description="Issue a prompt_reader key in Security first. Every pinned component needs its own grant. Telemetry ingest keys never receive prompt content."
                onSubmit={async (f) => {
                  await json(api, pbase + "/read-grants", {
                    actor: f.actor,
                    environment,
                    workflow: f.workflow,
                    policy_id: f.policy,
                    expires_at: new Date(String(f.expiry)).toISOString(),
                    approval_ref: f.approval,
                    expected_revision: Number(f.revision),
                  });
                  const r = await api(base + "/prompt-read-grants");
                  setGrants(r.items);
                  setGrantCursor(r.next_cursor);
                  return "Scoped read grant saved.";
                }}
              >
                <Field label="Reader principal ID" name="actor" required />
                <Field
                  label="Reader consumer workflow"
                  name="workflow"
                  defaultValue={selected.workflow}
                  required
                />
                {policyOptions.length ? (
                  <Select label="Reader content policy" name="policy">
                    {policyOptions}
                  </Select>
                ) : (
                  <Field label="Reader policy ID" name="policy" required />
                )}
                <Field
                  label="Read grant expiry"
                  name="expiry"
                  type="datetime-local"
                  required
                />
                <Field
                  label="Expected grant revision (zero for new)"
                  name="revision"
                  type="number"
                  min="0"
                  defaultValue="0"
                  required
                />
                <Field
                  label="Read authorization reference"
                  name="approval"
                  required
                />
              </Form>
            </Editor>
          )}
        </div>
      )}
      {admin && (
        <Editor title="Current prompt read grants">
          {grants.length === 0 ? (
            <p>No read grants.</p>
          ) : (
            grants.map((g) => (
              <div key={g.id}>
                <p>
                  <code>{g.actor}</code> · prompt <code>{g.prompt_id}</code> ·{" "}
                  {g.workflow} · revision {g.revision} ·{" "}
                  {g.enabled ? "enabled" : "revoked"}
                </p>
                {g.enabled && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      action(async () => {
                        const r = await api(
                          base + "/prompt-read-grants/" + g.id,
                          { method: "DELETE" },
                        );
                        setGrants((old) =>
                          old.map((x) => (x.id === g.id ? { ...x, ...r } : x)),
                        );
                      })
                    }
                  >
                    Revoke grant {g.id}
                  </button>
                )}
              </div>
            ))
          )}
          {grantCursor && (
            <button
              disabled={busy}
              onClick={() =>
                action(async () => {
                  const r = await api(
                    base +
                      "/prompt-read-grants?after=" +
                      encodeURIComponent(grantCursor),
                  );
                  setGrants((old) => [...old, ...r.items]);
                  setGrantCursor(r.next_cursor);
                })
              }
            >
              More read grants
            </button>
          )}
        </Editor>
      )}
    </section>
  );
}

function Playground({
  api,
  base,
  prompt,
  versions,
  environment,
  datasets,
}: {
  api: API;
  base: string;
  prompt: Data;
  versions: Data[];
  environment: string;
  datasets: Data[];
}) {
  const [plan, setPlan] = useState<Data | null>(null);
  const [provider, setProvider] = useState("local");
  const choices = datasets.filter(
    (d) => d.workflow === prompt.workflow || d.workflow_id === prompt.workflow,
  );
  return (
    <Editor title="Compare in a budgeted experiment">
      <Form
        title="Prompt playground plan"
        submit="Prepare frozen comparison"
        description="Select two versions and a dataset. Preparation makes no provider calls; approve and queue the plan separately in Experiments. Local costs are an operator-declared upper bound."
        onSubmit={async (f) => {
          const dataset = choices.find((d) => d.id === f.dataset);
          if (!dataset) throw new Error("DATASET_REQUIRED");
          const sampling = (prefix: string) =>
            provider === "anthropic"
              ? {}
              : Object.fromEntries(
                  [
                    ["temperature", f[prefix + "_temperature"]],
                    ["top_p", f[prefix + "_top_p"]],
                  ]
                    .filter(
                      ([, value]) => typeof value === "string" && value !== "",
                    )
                    .map(([key, value]) => [key, Number(value)]),
                );
          const model = (key: string, name: FormDataEntryValue) => ({
            key,
            provider,
            model: name,
            max_output_tokens: Number(f.tokens),
            ...(provider === "local"
              ? { local_cost_upper_usd: String(f.local_cost) }
              : {}),
          });
          setPlan(
            await json(api, base + "/prompts/playground", {
              dataset_id: dataset.id,
              purpose: f.purpose,
              specification: {
                workflow: prompt.workflow,
                dataset_hash: dataset.hash,
                baseline: model("baseline", f.baseline_model),
                candidates: [model("candidate", f.candidate_model)],
                metric: {
                  kind: f.metric,
                  minimum: Number(f.minimum),
                  min_test_samples: Number(f.samples),
                  min_test_groups: Number(f.groups),
                  ...(f.output_field ? { output_field: f.output_field } : {}),
                },
                split: f.split,
                budget: {
                  max_usd: String(f.budget),
                  max_requests: Number(f.requests),
                  max_wall_seconds: 300,
                },
                approved_providers: [provider],
              },
              selections: [
                {
                  model_key: "baseline",
                  version_id: f.baseline,
                  environment,
                  prompt_variables: JSON.parse(String(f.baseline_vars)),
                  sampling_options: sampling("baseline"),
                },
                {
                  model_key: "candidate",
                  version_id: f.candidate,
                  environment,
                  prompt_variables: JSON.parse(String(f.candidate_vars)),
                  sampling_options: sampling("candidate"),
                },
              ],
            }),
          );
          return "Frozen comparison ready for separate budget approval. No inference performed.";
        }}
      >
        <Select label="Comparison dataset" name="dataset" required>
          <option value="">Choose a frozen dataset</option>
          {choices.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name || d.workflow || d.workflow_id} · {d.id}
            </option>
          ))}
        </Select>
        <Select label="Baseline prompt version" name="baseline">
          {versions.map((v) => (
            <option key={v.id} value={v.id}>
              v{v.ordinal}
            </option>
          ))}
        </Select>
        <Select label="Candidate prompt version" name="candidate">
          {versions.map((v) => (
            <option key={v.id} value={v.id}>
              v{v.ordinal}
            </option>
          ))}
        </Select>
        <JSONField
          label="Baseline fixed variables (exclude input)"
          name="baseline_vars"
        />
        <JSONField
          label="Candidate fixed variables (exclude input)"
          name="candidate_vars"
        />
        <Select
          label="Experiment provider"
          name="provider"
          value={provider}
          onChange={(e) => setProvider(e.target.value)}
        >
          <option value="local">Explicit local endpoint</option>
          <option value="openai">OpenAI</option>
          <option value="anthropic">Anthropic</option>
        </Select>
        <Field
          label="Baseline native model"
          name="baseline_model"
          required
          defaultValue="synthetic-baseline"
        />
        <Field
          label="Candidate native model"
          name="candidate_model"
          required
          defaultValue="synthetic-candidate"
        />
        {provider === "local" && (
          <Field
            label="Local per-request cost upper bound (USD)"
            name="local_cost"
            type="number"
            min="0"
            step="any"
            defaultValue="0"
            required
          />
        )}
        <Select label="Quality criterion" name="metric">
          <option value="nonempty">Nonempty output (structural only)</option>
          <option value="exact_label">Exact reviewed label</option>
          <option value="reference_match">
            Reference match (agreement only)
          </option>
          <option value="field_f1">Reviewed field F1</option>
        </Select>
        {provider !== "anthropic" && (
          <>
            <Field
              label="Baseline temperature (optional)"
              name="baseline_temperature"
              type="number"
              min="0"
              max="2"
              step="any"
            />
            <Field
              label="Candidate temperature (optional)"
              name="candidate_temperature"
              type="number"
              min="0"
              max="2"
              step="any"
            />
            <Field
              label="Baseline top-p (optional)"
              name="baseline_top_p"
              type="number"
              min="0"
              max="1"
              step="any"
            />
            <Field
              label="Candidate top-p (optional)"
              name="candidate_top_p"
              type="number"
              min="0"
              max="1"
              step="any"
            />
          </>
        )}
        <Field
          label="Minimum criterion score"
          name="minimum"
          type="number"
          min="0"
          max="1"
          step="0.01"
          defaultValue="0.9"
          required
        />
        <Field
          label="Structured output field to compare (optional)"
          name="output_field"
          pattern="[A-Za-z_][A-Za-z0-9_]{0,63}"
          hint="For example, label. Required schema checks still validate the whole JSON response."
        />
        <Field
          label="Minimum evaluated cases"
          name="samples"
          type="number"
          min="1"
          max="10000"
          defaultValue="30"
          required
        />
        <Field
          label="Minimum independent groups"
          name="groups"
          type="number"
          min="1"
          max="10000"
          defaultValue="3"
          required
        />
        <Select label="Dataset split" name="split">
          <option value="validation">Validation — development evidence</option>
          <option value="test">Test — consumes protected groups</option>
        </Select>
        <Field
          label="Maximum experiment spend (USD)"
          name="budget"
          type="number"
          min="0"
          max="10000"
          step="any"
          defaultValue="0"
          required
        />
        <Field
          label="Maximum experiment requests"
          name="requests"
          type="number"
          min="1"
          max="10000"
          defaultValue="100"
          required
        />
        <Field
          label="Maximum output tokens"
          name="tokens"
          type="number"
          min="16"
          max="16384"
          defaultValue="256"
          required
        />
        <Field
          label="Experiment purpose"
          name="purpose"
          required
          defaultValue="compare-reviewed-prompt-versions"
        />
        <Check
          name="authorized"
          label="I authorize placing these fixed variables and prompt versions in an encrypted experiment plan."
          required
        />
      </Form>
      {plan && (
        <p role="status">
          Plan <code>{plan.id}</code> is {plan.status}. Open Experiments to
          review its bounds and approve it.
        </p>
      )}
    </Editor>
  );
}
