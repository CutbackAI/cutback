import React, { useEffect, useRef, useState } from "react";
import type { components } from "./api.generated";
import { type API, type Data, explain, json, Field } from "./ui";
import { gatewayModeledUSD as money } from "./gateway-money";
import { CaseEvidence } from "./case-evidence";

type Draft = components["schemas"]["CompressionDraftInput"];
type Binding = NonNullable<Draft["configuration_binding"]>;

export function Compression({
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
  const base = `/projects/${project}`;
  const [datasets, setDatasets] = useState<Data[]>([]);
  const [dataset, setDataset] = useState("");
  const [history, setHistory] = useState<Data[]>([]);
  const [offset, setOffset] = useState(0);
  const [more, setMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [binding, setBinding] = useState<Binding | null>(null);
  const [plan, setPlan] = useState<Data | null>(null);
  const [run, setRun] = useState<Data | null>(null);
  const [approved, setApproved] = useState(false);
  const [queued, setQueued] = useState(false);
  const [purpose, setPurpose] = useState("");
  const [proposal, setProposal] = useState<Data | null>(null);
  const alive = useRef(true);
  const working = useRef(false);
  const generation = useRef(0);
  const bindingGeneration = useRef(0);

  useEffect(() => {
    alive.current = true;
    Promise.all([
      api(`${base}/datasets?limit=100`),
      api(`${base}/experiments?limit=50`),
    ])
      .then(([data, runs]) => {
        if (!alive.current) return;
        setDatasets(data);
        setDataset(
          data.find((d: Data) => d.provenance?.independent_labels)?.id || "",
        );
        setHistory(
          runs.filter((r: Data) => r.specification?.compression_execution),
        );
        setOffset(runs.length);
        setMore(runs.length === 50);
      })
      .catch((e) => {
        if (alive.current) setError(explain(e));
      })
      .finally(() => {
        if (alive.current) setLoading(false);
      });
    return () => {
      alive.current = false;
      generation.current++;
      bindingGeneration.current++;
    };
  }, [api, base]);

  async function act(operation: () => Promise<void>) {
    if (working.current) return;
    working.current = true;
    setBusy(true);
    setError("");
    try {
      await operation();
    } catch (e) {
      if (alive.current) setError(explain(e));
    } finally {
      working.current = false;
      if (alive.current) setBusy(false);
    }
  }
  function prepared(value: Data) {
    if (!alive.current) return;
    generation.current++;
    setPlan(value);
    setRun(null);
    setApproved(false);
    setQueued(false);
    setProposal(null);
    setPurpose("");
  }
  async function inspect(id: string, historical = false) {
    const version = ++generation.current;
    const value = await api(`${base}/experiments/${id}`);
    if (!alive.current || version !== generation.current) return;
    setRun(value);
    setProposal(null);
    setPurpose("");
    if (historical) {
      setPlan(null);
      setApproved(false);
      setQueued(false);
    }
  }
  async function readBinding(file?: File) {
    const version = ++bindingGeneration.current;
    setBinding(null);
    try {
      if (!file || file.size > 16384)
        throw new Error("Choose a small inspect-config JSON receipt.");
      const value = JSON.parse(await file.text());
      if (
        !value.configuration_binding?.compression_contract_sha256 ||
        !value.configuration_binding?.source_tree_hash
      )
        throw new Error("Inspect the clean compression settings first.");
      if (alive.current && version === bindingGeneration.current)
        setBinding(value.configuration_binding);
    } catch (e) {
      if (alive.current && version === bindingGeneration.current)
        setError(explain(e));
    }
  }
  const confirmation = Boolean(run?.specification?.predecessor_run_id);
  const results = run?.results;
  return (
    <section aria-labelledby="compression-title">
      <h1 id="compression-title">Context compression</h1>
      <p>
        Compare original context with exact duplicate-line removal and bounded
        query-based line selection. The native model, instruction and output
        ceiling stay fixed. A smaller input can lose essential facts; answer
        quality and complete modeled component costs decide whether to continue.
      </p>
      <p>
        Use an authorized dataset whose inputs contain a query and context.
        Preparing a plan makes no provider call. Byte reduction does not
        establish token savings, semantic equivalence or customer savings.
      </p>
      {error && <p role="alert">{error}</p>}
      {loading && <p role="status">Loading compression datasets and runs…</p>}
      {canWrite && !loading && (
        <form
          key={binding?.sha256 || "unbound"}
          onSubmit={(event) => {
            event.preventDefault();
            const f = new FormData(event.currentTarget);
            void act(async () => {
              const body: Draft = {
                dataset_id: String(f.get("dataset")),
                baseline: {
                  key: "baseline",
                  provider: String(f.get("provider")) as "openai" | "anthropic",
                  model: String(f.get("model")),
                  instruction: String(f.get("instruction") || ""),
                  max_input_tokens: Number(f.get("inputLimit")),
                  max_output_tokens: Number(f.get("outputLimit")),
                },
                settings: binding?.settings || {
                  version: "cutback.extractive-context.v1",
                  mode: "identity",
                  max_context_bytes: Number(f.get("contextLimit")),
                },
                configuration_binding: binding,
                metric: {
                  kind: "exact_label",
                  minimum: Number(f.get("minimum")),
                  min_test_samples: Number(f.get("samples")),
                  min_test_groups: 3,
                  max_regression: 0,
                  cutoff: 10,
                  numeric_tolerance: 0,
                },
                budget: {
                  max_usd: String(f.get("budget")),
                  max_requests: Number(f.get("requests")),
                  max_wall_seconds: 300,
                },
                assumed_usd_per_cpu_hour: String(f.get("cpu")) || null,
                retain_case_outputs: f.get("retain") === "on",
                max_query_bytes: 1024,
              };
              prepared(await json(api, `${base}/compression/draft`, body));
            });
          }}
        >
          <h2>1. Prepare a comparison</h2>
          {!datasets.length && (
            <p>
              Import independently labeled query/context examples in
              Integrations first.
            </p>
          )}
          <label>
            Question dataset
            <select
              name="dataset"
              aria-label="Question dataset"
              required
              value={dataset}
              onChange={(e) => setDataset(e.target.value)}
            >
              <option value="">Choose a dataset</option>
              {datasets.map((d) => (
                <option
                  key={d.id}
                  value={d.id}
                  disabled={!d.provenance?.independent_labels}
                >
                  {d.workflow} · {d.id.slice(0, 8)}
                </option>
              ))}
            </select>
          </label>
          {datasets.length >= 100 && (
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                void act(async () => {
                  const next = await api(
                    `${base}/datasets?limit=100&offset=${datasets.length}`,
                  );
                  if (alive.current) setDatasets((old) => [...old, ...next]);
                })
              }
            >
              Load more datasets
            </button>
          )}
          <label>
            Source configuration inspection (optional)
            <input
              type="file"
              accept=".json,application/json"
              onChange={(e) => {
                const file = e.currentTarget.files?.[0];
                e.currentTarget.value = "";
                void readBinding(file);
              }}
            />
          </label>
          <p>
            For a source diff, use{" "}
            <code>
              cutback compression inspect-config APP_ROOT compression.json
              --output ../inspection.json
            </code>
            . The exact clean source is checked again before preparing a diff.
          </p>
          {binding && (
            <p role="status">Bound source configuration: {binding.file}</p>
          )}
          <div className="form-grid">
            <label>
              Provider
              <select name="provider" defaultValue="openai">
                <option>openai</option>
                <option>anthropic</option>
              </select>
            </label>
            <Field
              label="Native model"
              name="model"
              defaultValue="gpt-4.1-mini"
              required
            />
            <label>
              Application instruction (optional)
              <textarea name="instruction" maxLength={16000} />
            </label>
            <Field
              label="Native input ceiling"
              name="inputLimit"
              type="number"
              min={1}
              max={100000}
              defaultValue={20000}
              required
            />
            <Field
              label="Native output ceiling"
              name="outputLimit"
              type="number"
              min={16}
              max={16384}
              defaultValue={128}
              required
            />
            <Field
              label="Original context byte ceiling"
              name="contextLimit"
              type="number"
              min={64}
              max={12288}
              defaultValue={binding?.settings.max_context_bytes || 1024}
              disabled={Boolean(binding)}
              required
            />
            <Field
              label="Minimum exact-answer score"
              name="minimum"
              type="number"
              min={0}
              max={1}
              step="0.01"
              defaultValue="0.9"
              required
            />
            <Field
              label="Minimum independent questions"
              name="samples"
              type="number"
              min={1}
              max={10000}
              defaultValue={30}
              required
            />
            <Field
              label="Native API budget (USD)"
              name="budget"
              defaultValue="1"
              pattern="[0-9]+(\.[0-9]+)?"
              required
            />
            <Field
              label="Request ceiling"
              name="requests"
              type="number"
              min={1}
              max={10000}
              defaultValue={200}
              required
            />
            <Field
              label="Assumed CPU USD per hour (optional)"
              name="cpu"
              placeholder="Unknown"
              pattern="[0-9]+(\.[0-9]+)?"
            />
          </div>
          <p>
            Measured compression and lineage-preparation CPU is included under
            the declared rate. Unknown CPU cost prevents automatic economic
            selection. Infrastructure and full task costs remain unmeasured.
          </p>
          <label>
            <input name="retain" type="checkbox" /> Retain encrypted outputs and
            compressed input under the existing policy
          </label>
          <button disabled={busy || !datasets.length}>
            Prepare compression comparison
          </button>
        </form>
      )}
      {plan && (
        <section aria-label="Reviewed compression plan">
          <h2>
            {plan.split === "test"
              ? "4. Review untouched-test confirmation"
              : "2. Review the exact plan"}
          </h2>
          <p>
            {plan.sample_n} questions · {plan.requests} native requests · up to{" "}
            {money(plan.worst_case_modeled_usd)} native API cost.
          </p>
          <p>
            Budget fit: {plan.fits_budget ? "Yes" : "No"}. Plan hash:{" "}
            <code>{plan.plan_hash}</code>
          </p>
          {canApprove && (
            <button
              disabled={busy || approved || !plan.fits_budget}
              onClick={() =>
                void act(async () => {
                  await json(
                    api,
                    `${base}/experiments/plans/${plan.id}/approve`,
                    {
                      plan_hash: plan.plan_hash,
                      approval_ref: "dashboard-compression-reviewed",
                    },
                  );
                  if (alive.current) setApproved(true);
                })
              }
            >
              Approve this exact compression budget
            </button>
          )}
          {canWrite && (
            <button
              disabled={busy || !approved || queued}
              onClick={() =>
                void act(async () => {
                  setQueued(true);
                  const value = await json(api, `${base}/experiments`, {
                    plan_id: plan.id,
                  });
                  await inspect(value.id);
                })
              }
            >
              Queue approved compression run
            </button>
          )}
          <button
            disabled={busy}
            onClick={() =>
              void act(async () => {
                const value = await api(
                  `${base}/experiments/plans/${plan.id}/execution`,
                );
                if (value.run_id) await inspect(value.run_id);
                else if (alive.current) {
                  setApproved(value.approval_status === "approved");
                  setError(
                    "No queued run was found. Inspect Experiments before preparing another plan.",
                  );
                }
              })
            }
          >
            Reconcile plan execution
          </button>
          <p>
            Execution requires an enabled worker. An uncertain billable
            operation is never retried automatically.
          </p>
        </section>
      )}
      {run && (
        <section aria-label="Compression run results">
          <h2>
            {confirmation
              ? "5. Review independent confirmation"
              : "3. Review validation"}
          </h2>
          <p>
            {run.status} · {run.requests} requests · known native spend{" "}
            {money(results?.known_spend_subtotal_usd)}. Complete billing:{" "}
            {results?.billing_complete === true
              ? "Yes"
              : "Unknown or incomplete"}
            .
          </p>
          <button
            disabled={busy}
            onClick={() => void act(() => inspect(run.id))}
          >
            Refresh compression run
          </button>
          {results?.compression && (
            <div
              className="table-scroll"
              role="region"
              aria-label="Compression comparison"
              tabIndex={0}
            >
              <table>
                <thead>
                  <tr>
                    <th>Configuration</th>
                    <th>Prepared context bytes</th>
                    <th>Compressed context bytes</th>
                    <th>CPU ms</th>
                    <th>Modeled components</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(
                    results.compression.models as Record<string, Data>,
                  ).map(([key, v]) => (
                    <tr key={key}>
                      <th scope="row">{key}</th>
                      <td>{v.input_context_bytes}</td>
                      <td>{v.output_context_bytes}</td>
                      <td>{(v.cpu_ns / 1e6).toFixed(3)}</td>
                      <td>{money(v.modeled_component_cost_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p>
            Context-byte totals count distinct prepared inputs, including
            whole-corpus lineage checks; they are not dispatched token counts.
          </p>
          <ul>
            {results?.recommendations?.map((d: Data) => (
              <li key={d.candidate.key}>
                {d.candidate.key}: answer quality{" "}
                {d.compression_quality_assessment_passed
                  ? "passed"
                  : "requires review"}
                ; lower modeled component cost{" "}
                {d.cheaper_on_sample === true
                  ? "yes"
                  : d.cheaper_on_sample === false
                    ? "no"
                    : "unknown"}
                ; statistical gate{" "}
                {d.statistical_gate_passed ? "passed" : "not established"}.
              </li>
            ))}
          </ul>
          {canApprove && results?.case_outputs_retained && (
            <CaseEvidence key={run.id} api={api} base={base} runId={run.id} />
          )}
          {!confirmation && canWrite && run.status === "COMPLETED" && (
            <button
              disabled={
                busy ||
                !results?.recommendations?.some(
                  (d: Data) => d.compression_automation_eligible,
                )
              }
              onClick={() =>
                void act(async () => {
                  prepared(
                    await json(api, `${base}/compression/confirmation`, {
                      validation_run_id: run.id,
                      budget: run.specification.budget,
                    }),
                  );
                })
              }
            >
              Prepare selected candidate confirmation
            </button>
          )}
          {confirmation && canApprove && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void act(async () => {
                  const value = await json(
                    api,
                    `${base}/compression/${run.id}/proposal`,
                    { purpose },
                  );
                  if (alive.current) setProposal(value);
                });
              }}
            >
              <Field
                label="Purpose for reading source proposal"
                name="purpose"
                value={purpose}
                onChange={(e) => setPurpose(e.target.value)}
                required
                maxLength={100}
              />
              <button disabled={busy || !purpose}>
                Read authorized compression proposal
              </button>
            </form>
          )}
          {proposal && (
            <section aria-label="Compression source proposal">
              <h3>Review the settings change</h3>
              <p>
                {proposal.decision}. Application checks and separate production
                approval are required.
              </p>
              <pre tabIndex={0}>{JSON.stringify(proposal, null, 2)}</pre>
              <button
                onClick={() => {
                  const url = URL.createObjectURL(
                    new Blob([JSON.stringify(proposal, null, 2) + "\n"], {
                      type: "application/json",
                    }),
                  );
                  const a = document.createElement("a");
                  a.href = url;
                  a.download = "compression-proposal.json";
                  a.click();
                  URL.revokeObjectURL(url);
                }}
              >
                Download compression proposal
              </button>
            </section>
          )}
        </section>
      )}
      <section aria-label="Compression run history">
        <h2>Previous compression runs</h2>
        <button
          disabled={busy || loading}
          onClick={() =>
            void act(async () => {
              const rows = await api(`${base}/experiments?limit=50`);
              if (alive.current) {
                setHistory(
                  rows.filter(
                    (r: Data) => r.specification?.compression_execution,
                  ),
                );
                setOffset(rows.length);
                setMore(rows.length === 50);
              }
            })
          }
        >
          Refresh compression history
        </button>
        {!loading && !history.length && <p>No retained compression runs.</p>}
        <ul>
          {history.map((r) => (
            <li key={r.id}>
              <button
                disabled={busy}
                onClick={() => void act(() => inspect(r.id, true))}
              >
                {r.status} · {r.id.slice(0, 8)}
              </button>
            </li>
          ))}
        </ul>
        {more && (
          <button
            disabled={busy}
            onClick={() =>
              void act(async () => {
                const rows = await api(
                  `${base}/experiments?limit=50&offset=${offset}`,
                );
                if (alive.current) {
                  setHistory((old) => [
                    ...old,
                    ...rows.filter(
                      (r: Data) => r.specification?.compression_execution,
                    ),
                  ]);
                  setOffset(offset + rows.length);
                  setMore(rows.length === 50);
                }
              })
            }
          >
            Load more run history
          </button>
        )}
      </section>
    </section>
  );
}
