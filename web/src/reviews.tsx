import React, { useRef, useState } from "react";
import {
  type API,
  type Data,
  Form,
  Field,
  Select,
  Check,
  Editor,
  json,
  explain,
  list,
} from "./ui";

export function Reviews({
  api,
  base,
  data,
  admin,
  analyst,
  reload,
  traceSource,
}: {
  api: API;
  base: string;
  data: Data;
  admin: boolean;
  analyst: boolean;
  reload: () => void;
  traceSource?: string;
}) {
  const [rows, setRows] = useState<Data[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [extraQueues, setExtraQueues] = useState<Data[]>([]);
  const [queueCursor, setQueueCursor] = useState<string | null>(
    data.queues?.next_cursor || null,
  );
  const [selected, setSelected] = useState<Data | null>(null);
  const [lease, setLease] = useState<Data | null>(null);
  const [content, setContent] = useState<Data | null>(null);
  const [adjudication, setAdjudication] = useState<Data | null>(null);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const version = useRef(0);
  const queues = [...(data.queues?.items || []), ...extraQueues].filter(
    (q, i, all) => all.findIndex((v) => v.id === q.id) === i,
  );
  const qbase = selected ? base + "/reviews/" + selected.id : "";
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
  async function open(q: Data) {
    const current = ++version.current;
    setSelected(q);
    setRows([]);
    setContent(null);
    setLease(null);
    setAdjudication(null);
    setCursor(null);
    const result = await api(base + "/reviews/" + q.id + "/items");
    if (version.current !== current) return;
    setRows(result.items);
    setCursor(result.next_cursor);
  }
  return (
    <section aria-label="Blind review workspace">
      <p>
        Assign independent reviewers, resolve disagreements, and freeze a new
        regression dataset. Opening a review makes its groups development
        evidence. Reviewed groups cannot become untouched confirmation data by
        renaming or reimporting them.
      </p>
      <p className="hint">
        The server enforces blind answers. Separate credentials do not prove
        separate humans; independent labeling requires an explicit owner
        attestation.
      </p>
      {admin && <h2>Create a review queue</h2>}
      {admin && (
        <CreateQueue
          api={api}
          base={base}
          policies={data.policies || []}
          datasets={data.datasets || []}
          principals={data.principals || { items: [] }}
          traceSource={traceSource}
          created={async (q) => {
            reload();
            await open(q);
          }}
        />
      )}
      <h2>Review queues</h2>
      {!queues.length && (
        <p className="empty">
          No review queues. Import an authorized dataset, then assign reviewers
          with project analyst access.
        </p>
      )}
      <ul className="review-queue-list">
        {queues.map((q) => (
          <li key={q.id}>
            <strong>{q.workflow}</strong> · {q.mode} · {q.status} ·{" "}
            {q.required_reviewers} reviewers
            <button disabled={busy} onClick={() => action(() => open(q))}>
              Open review {q.id.slice(0, 8)}
            </button>
          </li>
        ))}
      </ul>
      {queueCursor && (
        <button
          disabled={busy}
          onClick={() =>
            action(async () => {
              const result = await api(base + "/reviews?before=" + queueCursor);
              setExtraQueues((v) => [...v, ...result.items]);
              setQueueCursor(result.next_cursor);
            })
          }
        >
          Older review queues
        </button>
      )}
      {notice && <p role="alert">{notice}</p>}
      {selected && (
        <section key={selected.id} aria-label="Selected review queue">
          <h2>{selected.workflow} review</h2>
          <p>
            {selected.mode} · frozen rubric {selected.version_hash.slice(0, 12)}
          </p>
          {admin && selected.status === "open" && (
            <Editor title="Close this review queue">
              <Form
                title="Close frozen review queue"
                submit="Close queue and release leases"
                onSubmit={async (f) => {
                  await json(api, qbase + "/close", {
                    queue_hash: selected.version_hash,
                    purpose: f.purpose,
                  });
                  ++version.current;
                  setLease(null);
                  setContent(null);
                  setAdjudication(null);
                  setRows([]);
                  setSelected({ ...selected, status: "closed" });
                  reload();
                  return "Queue closed. Existing judgments are preserved.";
                }}
              >
                <Field
                  label="Queue closure purpose"
                  name="purpose"
                  required
                  maxLength={128}
                />
              </Form>
            </Editor>
          )}
          {analyst && selected.status === "open" && (
            <button
              disabled={busy}
              onClick={() =>
                action(async () => {
                  ++version.current;
                  const result = await json(api, qbase + "/claim", {});
                  setLease(result);
                  setContent(null);
                  setAdjudication(null);
                })
              }
            >
              Claim next blind review
            </button>
          )}
          {lease && (
            <>
              <p role="status">
                Assigned item {lease.item_id.slice(0, 8)}. Lease expires{" "}
                {new Date(lease.expires_at).toLocaleString()}. Read the source
                with a declared purpose before judging it.
              </p>
              <Form
                key={lease.lease_id}
                title="Read assigned example"
                submit="Read my review example"
                onSubmit={async (f) => {
                  const current = version.current;
                  const result = await json(
                    api,
                    qbase + "/items/" + lease.item_id + "/content",
                    { purpose: f.purpose },
                  );
                  if (version.current !== current)
                    throw new Error(
                      "Review selection changed. Read the currently assigned item.",
                    );
                  setContent(result);
                  return "Example loaded for this lease. Peer answers and source gold are withheld.";
                }}
              >
                <Field
                  label="Review read purpose"
                  name="purpose"
                  required
                  maxLength={128}
                />
              </Form>
              {content && (
                <>
                  <Content value={content} />
                  <Form
                    key={lease.lease_id + "-vote"}
                    title="Submit immutable review"
                    submit="Submit my judgment"
                    onSubmit={async (f) => {
                      const decision = String(f.decision);
                      const result = await json(
                        api,
                        qbase + "/items/" + lease.item_id + "/votes",
                        {
                          lease_id: lease.lease_id,
                          queue_hash: content.queue_hash,
                          decision,
                          gold:
                            decision === "exclude"
                              ? null
                              : JSON.parse(String(f.gold)),
                          rationale: f.rationale,
                        },
                      );
                      setContent(null);
                      setLease(null);
                      await open(selected);
                      return "Judgment saved. Item state: " + result.state;
                    }}
                  >
                    <Judgment />
                  </Form>
                </>
              )}
              <button
                disabled={busy}
                onClick={() =>
                  action(async () => {
                    await json(
                      api,
                      qbase + "/items/" + lease.item_id + "/release",
                      {
                        lease_id: lease.lease_id,
                        purpose: "reviewer-released-assignment",
                      },
                    );
                    setLease(null);
                    setContent(null);
                    await open(selected);
                  })
                }
              >
                Release this assignment
              </button>
            </>
          )}
          <h3>Items and disagreements</h3>
          <ul>
            {rows.map((r) => (
              <li key={r.id}>
                {r.id.slice(0, 8)} · {r.state}
                {analyst && r.state === "adjudication" && (
                  <Form
                    title={"Read disagreement " + r.id.slice(0, 8)}
                    submit="Read as assigned adjudicator"
                    onSubmit={async (f) => {
                      const current = version.current;
                      const result = await json(
                        api,
                        qbase + "/items/" + r.id + "/adjudication-content",
                        { purpose: f.purpose },
                      );
                      if (version.current !== current)
                        throw new Error(
                          "Review selection changed. Reopen the disagreement.",
                        );
                      setAdjudication({ ...result, item_id: r.id });
                      return "Disagreement loaded for the designated adjudicator.";
                    }}
                  >
                    <Field
                      label="Adjudication read purpose"
                      name="purpose"
                      required
                      maxLength={128}
                    />
                  </Form>
                )}
              </li>
            ))}
          </ul>
          {cursor && (
            <button
              disabled={busy}
              onClick={() =>
                action(async () => {
                  const result = await api(qbase + "/items?before=" + cursor);
                  setRows((v) => [...v, ...result.items]);
                  setCursor(result.next_cursor);
                })
              }
            >
              More review items
            </button>
          )}
          {adjudication && (
            <section
              key={adjudication.item_id}
              aria-label="Authorized adjudication"
            >
              <Content value={adjudication} />
              <h3>Completed blind answers</h3>
              {adjudication.votes.map((v: Data, i: number) => (
                <pre tabIndex={0} className="review-text" key={v.id}>
                  Reviewer {i + 1}
                  {"\n"}
                  {JSON.stringify(v.judgment, null, 2)}
                </pre>
              ))}
              <Form
                title="Resolve the disagreement"
                submit="Save immutable adjudication"
                onSubmit={async (f) => {
                  await json(
                    api,
                    qbase + "/items/" + adjudication.item_id + "/adjudicate",
                    {
                      queue_hash: adjudication.queue_hash,
                      decision: f.decision,
                      gold:
                        f.decision === "exclude"
                          ? null
                          : JSON.parse(String(f.gold)),
                      rationale: f.rationale,
                    },
                  );
                  setAdjudication(null);
                  await open(selected);
                  return "Adjudication saved.";
                }}
              >
                <Judgment />
              </Form>
            </section>
          )}
          {admin && selected.mode === "labeling" && (
            <Form
              key={selected.id + "-promotion"}
              title="Promote reviewed data"
              submit="Create frozen reviewed dataset"
              onSubmit={async (f) => {
                const result = await json(api, qbase + "/promote", {
                  queue_hash: selected.version_hash,
                  provenance_ref: f.provenance,
                  rights_confirmed: !!f.rights,
                  independent_reviewers_attested: !!f.independent,
                });
                setContent(null);
                setAdjudication(null);
                setLease(null);
                reload();
                setSelected({
                  ...selected,
                  status: "promoted",
                  dataset_id: result.id,
                });
                return (
                  "Frozen reviewed dataset created: " +
                  result.id +
                  ". Plans and prior evidence remain unchanged."
                );
              }}
            >
              <Field
                label="Reviewed dataset provenance"
                name="provenance"
                required
                maxLength={128}
              />
              <Check
                label="I confirm these reviewed inputs and labels are authorized under the queue policy."
                name="rights"
                required
              />
              <Check
                label="I attest these are independent human reviewers and independently produced labels."
                name="independent"
              />
            </Form>
          )}
          {admin && selected.mode === "calibration" && (
            <Form
              title="Read completed calibration"
              submit="Read calibration scores"
              onSubmit={async (f) => {
                const result = await json(api, qbase + "/calibration", {
                  purpose: f.purpose,
                });
                return (
                  "Exact matches: " +
                  Object.values(result.scores)
                    .map((s: any) => s.correct + "/" + s.total)
                    .join(", ") +
                  ". Generalization remains unknown. Calibration data cannot be promoted."
                );
              }}
            >
              <Field
                label="Calibration read purpose"
                name="purpose"
                required
                maxLength={128}
              />
            </Form>
          )}
        </section>
      )}
    </section>
  );
}

function Content({ value }: { value: Data }) {
  return (
    <section aria-label="Authorized review content">
      <h3>Frozen rubric</h3>
      <pre tabIndex={0} className="review-text">
        {value.rubric}
      </pre>
      <h3>Source input</h3>
      <pre tabIndex={0} className="review-text">
        {value.input}
      </pre>
      {value.reference !== null && (
        <>
          <h3>Historical reference, unverified</h3>
          <pre tabIndex={0} className="review-text">
            {value.reference}
          </pre>
        </>
      )}
      <p className="hint">
        Source gold and other reviewers’ answers are withheld during blind
        review. Never treat the historical output as an independent label.
      </p>
    </section>
  );
}

function Judgment() {
  const [decision, setDecision] = useState("label");
  return (
    <>
      <Select
        label="Review decision"
        name="decision"
        value={decision}
        onChange={(e) => setDecision(e.target.value)}
      >
        <option value="label">Supply a reviewed label</option>
        <option value="exclude">Exclude from dataset</option>
      </Select>
      <label className="field">
        Gold label as JSON
        <textarea
          name="gold"
          placeholder={'"reviewed label", ["label"], or {"field":"value"}'}
          maxLength={16000}
          required={decision === "label"}
          disabled={decision === "exclude"}
        />
      </label>
      <label className="field">
        Review rationale
        <textarea name="rationale" required maxLength={8000} />
      </label>
    </>
  );
}

function CreateQueue({
  api,
  base,
  datasets,
  policies,
  principals,
  created,
  traceSource,
}: {
  api: API;
  base: string;
  datasets: Data[];
  policies: Data[];
  principals: Data;
  created: (q: Data) => Promise<void>;
  traceSource?: string;
}) {
  const [source, setSource] = useState<Data | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [datasetID, setDatasetID] = useState("");
  const [reviewers, setReviewers] = useState<string[]>([]);
  return (
    <Editor title="Create a frozen review queue" open={!!traceSource}>
      <p>
        Choose up to 200 examples. Creation copies authorized content into
        encrypted review storage, freezes the rubric and assignments, and marks
        selected groups as development evidence. No model calls are made.
      </p>
      <Form
        title="Choose review source dataset"
        submit="List source examples"
        onSubmit={async (f) => {
          const result = await api(
            base + "/review-sources?dataset_id=" + f.dataset,
          );
          setSource(result);
          setDatasetID(String(f.dataset));
          setSelected([]);
          setNext(result.next_cursor);
        }}
      >
        <Select
          label="Review source dataset"
          name="dataset"
          required
          defaultValue=""
        >
          <option value="" disabled>
            Choose frozen dataset
          </option>
          {datasets.map((d) => (
            <option value={d.id} key={d.id}>
              {d.workflow_id || d.workflow} · {d.id.slice(0, 8)}
            </option>
          ))}
        </Select>
      </Form>
      <Form
        key={traceSource || "trace-source"}
        title="Choose captured trace content"
        submit="List captured review source"
        onSubmit={async (f) => {
          const result = await api(
            base + "/review-sources?trace_content_id=" + f.trace,
          );
          setSource(result);
          setDatasetID("trace:" + f.trace);
          setSelected([]);
          setNext(null);
        }}
      >
        <Field
          label="Captured step content ID"
          name="trace"
          defaultValue={traceSource || ""}
          required
          hint="Use Prepare review of this step in Traces. Metadata-only spans cannot supply missing content."
        />
      </Form>
      {source && (
        <>
          {source.items.some(
            (r: Data) => r.kind === "dataset_item" && !r.lineage_known,
          ) && (
            <Form
              key={datasetID + "-legacy"}
              title="Reconcile legacy dataset identities"
              submit="Reconcile as exposed development data"
              onSubmit={async (f) => {
                const dataset = datasets.find((d) => d.id === datasetID);
                const result = await json(
                  api,
                  base + "/datasets/" + datasetID + "/lineage/reconcile",
                  { immutable_hash: dataset?.hash, purpose: f.purpose },
                );
                return (
                  result.status +
                  ". Original hashes and splits are preserved; no fresh test claim is created."
                );
              }}
            >
              <p>
                Some legacy group identities are unknown. Reconciliation
                requires current encrypted source rights and conservatively
                marks recovered groups as exposed.
              </p>
              <Field
                label="Legacy reconciliation purpose"
                name="purpose"
                required
                maxLength={128}
              />
            </Form>
          )}
          <fieldset className="review-source-list">
            <legend>Select review examples ({selected.length}/200)</legend>
            {source.items.map((r: Data, i: number) => (
              <Check
                key={r.id}
                label={
                  "Example " +
                  (i + 1) +
                  " · " +
                  r.split +
                  " · " +
                  r.id.slice(0, 8)
                }
                name={r.id}
                checked={selected.includes(r.id)}
                disabled={!selected.includes(r.id) && selected.length >= 200}
                onChange={(e) =>
                  setSelected((v) =>
                    e.target.checked
                      ? [...v, r.id]
                      : v.filter((id) => id !== r.id),
                  )
                }
              />
            ))}
          </fieldset>
          {next && (
            <Form
              title="Load more source examples"
              submit="More source examples"
              onSubmit={async () => {
                const result = await api(
                  base +
                    "/review-sources?dataset_id=" +
                    datasetID +
                    "&after=" +
                    next,
                );
                setSource({
                  ...source,
                  items: [...source.items, ...result.items],
                });
                setNext(result.next_cursor);
              }}
            >
              <p>
                Only metadata is listed. Content is read under the approved
                policy when the queue is created.
              </p>
            </Form>
          )}
          <Form
            key={datasetID}
            title="Freeze rubric and assignments"
            submit="Create encrypted review queue"
            onSubmit={async (f) => {
              if (!selected.length || !f.authorized)
                throw new Error("Select examples and confirm review rights.");
              const q = await json(api, base + "/reviews", {
                policy_id: f.policy,
                workflow: source.workflow,
                environment: source.environment,
                sources: selected.map((id) => ({
                  kind: source.items.find((r: Data) => r.id === id).kind,
                  id,
                })),
                rubric: f.rubric,
                reviewers: reviewers.length ? reviewers : list(f.reviewers),
                adjudicator: f.adjudicator,
                required_reviewers: Number(f.required),
                mode: f.mode,
                provenance_ref: f.provenance,
              });
              await created(q);
              return "Frozen review queue created.";
            }}
          >
            <Select
              label="Review rights policy"
              name="policy"
              required
              defaultValue=""
            >
              <option value="" disabled>
                Choose active policy
              </option>
              {policies
                .filter(
                  (p) =>
                    p.enabled && Date.parse(p.policy.expires_at) > Date.now(),
                )
                .map((p) => (
                  <option value={p.id} key={p.id}>
                    {p.policy.approval_ref} · {p.id.slice(0, 8)}
                  </option>
                ))}
            </Select>
            <label className="field">
              Frozen review rubric
              <textarea name="rubric" required maxLength={16000} />
            </label>
            <fieldset>
              <legend>Assigned blind reviewers</legend>
              {principals.items
                .filter((p: Data) => p.can_review)
                .map((p: Data) => (
                  <Check
                    key={p.actor}
                    label={"Assign " + p.label}
                    name={"reviewer-" + p.actor}
                    checked={reviewers.includes(p.actor)}
                    onChange={(e) =>
                      setReviewers((v) =>
                        e.target.checked
                          ? [...v, p.actor]
                          : v.filter((a) => a !== p.actor),
                      )
                    }
                  />
                ))}
              {!principals.items.some((p: Data) => p.can_review) && (
                <p>
                  Issue separate analyst credentials in Security or grant
                  analyst access to current OIDC members.
                </p>
              )}
            </fieldset>
            <Select
              label="Separate adjudicator"
              name="adjudicator"
              required
              defaultValue={principals.current_actor || ""}
            >
              <option value="" disabled>
                Choose a current adjudicator
              </option>
              {principals.items.map((p: Data) => (
                <option
                  key={p.actor}
                  value={p.actor}
                  disabled={reviewers.includes(p.actor)}
                >
                  {p.label}
                </option>
              ))}
            </Select>
            {principals.truncated && (
              <Field
                label="Advanced reviewer actor IDs, comma separated"
                name="reviewers"
                hint="The principal directory is truncated. IDs are used only when no listed reviewer is selected. Never paste bearer tokens."
              />
            )}
            <Field
              label="Required blind reviewers"
              name="required"
              type="number"
              min={2}
              max={5}
              defaultValue={2}
              required
            />
            <Select label="Review mode" name="mode">
              <option value="labeling">Labeling for regression data</option>
              <option value="calibration">
                Calibration against hidden source gold
              </option>
            </Select>
            <Field
              label="Review provenance reference"
              name="provenance"
              required
              maxLength={128}
            />
            <Check
              label="I authorize these sources and the encrypted review copies under this policy."
              name="authorized"
              required
            />
          </Form>
          <details>
            <summary>Current scoped credential IDs</summary>
            <ul>
              {principals.items.map((p: Data) => (
                <li key={p.actor}>
                  {p.label} · {p.actor}
                </li>
              ))}
            </ul>
            <p>
              Issue reviewer credentials under Security, or use current project
              OIDC members. These IDs are not secrets; bearer tokens stay with
              their assigned reviewers.
            </p>
          </details>
        </>
      )}
    </Editor>
  );
}
