import React, { useEffect, useRef, useState } from "react";
import type { components } from "./api.generated";
import { type API, type Data, explain, json } from "./ui";
import { gatewayModeledUSD as money } from "./gateway-money";
import { CaseEvidence } from "./case-evidence";

type Draft = components["schemas"]["RetrievalDraftInput"];
type Corpus = components["schemas"]["RetrievalCorpus"];
type Binding = NonNullable<Draft["configuration_binding"]>;
type Proposal = components["schemas"]["RetrievalProposal"];
type JointDraft = components["schemas"]["JointRetrievalDraftInput"];
type Producer = NonNullable<JointDraft["producer_binding"]>;
type JointProposal = components["schemas"]["JointRetrievalProposal"];

export function Retrieval({
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
  const [historyOffset, setHistoryOffset] = useState(0);
  const [moreHistory, setMoreHistory] = useState(false);
  const [loading, setLoading] = useState(true);
  const [corpus, setCorpus] = useState<Corpus | null>(null);
  const [binding, setBinding] = useState<Binding | null>(null);
  const [joint, setJoint] = useState(false);
  const [producer, setProducer] = useState<Producer | null>(null);
  const [candidate, setCandidate] = useState("");
  const [confirmationBudget, setConfirmationBudget] = useState("1");
  const [confirmationRequests, setConfirmationRequests] = useState(200);
  const [plan, setPlan] = useState<Data | null>(null);
  const [run, setRun] = useState<Data | null>(null);
  const [proposal, setProposal] = useState<Proposal | JointProposal | null>(
    null,
  );
  const [approved, setApproved] = useState(false);
  const [queueAttempted, setQueueAttempted] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [purpose, setPurpose] = useState("");
  const alive = useRef(true);
  const working = useRef(false);
  const selection = useRef(0);
  const corpusVersion = useRef(0);
  const bindingVersion = useRef(0);

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
          data.find((row: Data) => row.provenance?.independent_labels)?.id ||
            "",
        );
        setHistory(
          runs.filter((r: Data) => r.specification?.retrieval_execution),
        );
        setHistoryOffset(runs.length);
        setMoreHistory(runs.length === 50);
      })
      .catch((e) => {
        if (alive.current) setError(explain(e));
      })
      .finally(() => {
        if (alive.current) setLoading(false);
      });
    return () => {
      alive.current = false;
      selection.current++;
      corpusVersion.current++;
      bindingVersion.current++;
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
  async function inspect(id: string, fromHistory = false) {
    const version = ++selection.current;
    const value = await api(`${base}/experiments/${id}`);
    if (alive.current && version === selection.current) {
      setRun(value);
      const isJoint =
        value.specification?.retrieval_execution?.version ===
        "cutback.joint-model-retrieval.v1";
      setCandidate(
        value.results?.recommendations?.find((row: Data) =>
          isJoint
            ? row.joint_model_retrieval_confirmation_eligible
            : row.retrieval_automation_eligible,
        )?.candidate.key || "",
      );
      setConfirmationBudget(value.specification?.budget?.max_usd || "1");
      setConfirmationRequests(value.specification?.budget?.max_requests || 200);
      if (fromHistory) {
        setPlan(null);
        setApproved(false);
        setQueueAttempted(false);
      }
      setProposal(null);
      setPurpose("");
    }
  }
  function prepared(value: Data) {
    if (!alive.current) return;
    selection.current++;
    setPlan(value);
    setRun(null);
    setProposal(null);
    setApproved(false);
    setQueueAttempted(false);
    setPurpose("");
  }
  async function loadCorpus(file?: File) {
    const version = ++corpusVersion.current;
    setCorpus(null);
    setError("");
    try {
      if (!file || file.size > 524288)
        throw new Error("Choose corpus JSON smaller than 512 KiB.");
      const value: Corpus = JSON.parse(await file.text());
      if (
        !Array.isArray(value.documents) ||
        !value.documents.length ||
        value.documents.length > 512
      )
        throw new Error("Corpus must contain between 1 and 512 documents.");
      if (alive.current && version === corpusVersion.current) setCorpus(value);
    } catch (e) {
      if (alive.current && version === corpusVersion.current)
        setError(explain(e));
    }
  }
  async function loadBinding(file?: File) {
    const version = ++bindingVersion.current;
    setBinding(null);
    setProducer(null);
    try {
      if (!file || file.size > 16384)
        throw new Error("Choose the small inspect-config JSON receipt.");
      const value = JSON.parse(await file.text());
      if (
        !value.configuration_binding?.source_tree_hash ||
        !value.configuration_binding?.retrieval_contract_sha256
      )
        throw new Error("Run inspect-config first.");
      if (joint && !value.producer_binding?.source_tree_hash)
        throw new Error(
          "Joint source review requires model-retrieval inspect-producer output.",
        );
      if (alive.current && version === bindingVersion.current) {
        setBinding(value.configuration_binding);
        setProducer(joint ? value.producer_binding : null);
      }
    } catch (e) {
      if (alive.current && version === bindingVersion.current)
        setError(explain(e));
    }
  }
  function download() {
    if (!proposal) return;
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(proposal, null, 2) + "\n"], {
        type: "application/json",
      }),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "retrieval-proposal.json";
    anchor.click();
    URL.revokeObjectURL(url);
  }
  const results = run?.results;
  const confirmation = Boolean(run?.specification?.predecessor_run_id);
  const runJoint =
    run?.specification?.retrieval_execution?.version ===
    "cutback.joint-model-retrieval.v1";
  const runRoute = runJoint ? "model-retrieval" : "retrieval";
  return (
    <section aria-labelledby="retrieval-title">
      <h1 id="retrieval-title">Retrieval optimization</h1>
      <p>
        Compare real local corpus retrieval, optionally searching native model
        choice and output limits together. Cutback prepares bounded alternatives
        and tests one selected configuration on untouched questions. No provider
        call starts when a plan is prepared.
      </p>
      <p>
        Answer labels and an authorized encrypted capture policy are required.
        This bounded text search supports up to 256 KiB of corpus text. It does
        not execute document instructions or application tools.
      </p>
      {error && <p role="alert">{error}</p>}
      {loading && <p role="status">Loading retrieval datasets and runs…</p>}
      {canWrite && !loading && (
        <form
          key={`${joint}-${binding?.sha256 || "unbound"}`}
          onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            void act(async () => {
              if (!corpus)
                throw new Error("Select an authorized corpus first.");
              const provider = String(form.get("provider")) as
                "openai" | "anthropic";
              const body: Draft | JointDraft = {
                dataset_id: dataset,
                corpus,
                configuration_binding: binding,
                baseline: {
                  key: "baseline",
                  provider: producer?.provider || provider,
                  model: producer?.model || String(form.get("model")),
                  instruction: String(form.get("instruction") || ""),
                  max_input_tokens: Number(form.get("inputLimit")),
                  max_output_tokens:
                    producer?.max_output_tokens ||
                    Number(form.get("outputLimit")),
                },
                settings: {
                  chunk_chars: Number(form.get("chunk")),
                  overlap_chars: Number(form.get("overlap")),
                  top_k: Number(form.get("topk")),
                  max_context_bytes: Number(form.get("contextLimit")),
                  k1: binding?.settings.k1 || "1.2",
                  b: binding?.settings.b || "0.75",
                  rerank: binding?.settings.rerank || "none",
                },
                metric: {
                  kind: "exact_label",
                  minimum: Number(form.get("minimum")),
                  min_test_samples: Number(form.get("samples")),
                  min_test_groups: 3,
                  max_regression: 0,
                  cutoff: 10,
                  numeric_tolerance: 0,
                },
                budget: {
                  max_usd: String(form.get("budget")),
                  max_requests: Number(form.get("requests")),
                  max_wall_seconds: 300,
                },
                assumed_usd_per_cpu_hour: String(form.get("cpu")) || null,
                retain_case_outputs: form.get("retain") === "on",
                max_query_bytes: 1024,
                ...(joint
                  ? { producer_binding: producer, max_candidates: 5 }
                  : {}),
              };
              prepared(
                await json(
                  api,
                  `${base}/${joint ? "model-retrieval" : "retrieval"}/draft`,
                  body,
                ),
              );
              if (alive.current) {
                corpusVersion.current++;
                setCorpus(null);
              }
            });
          }}
        >
          <h2>1. Prepare a comparison</h2>
          <label>
            <input
              type="checkbox"
              checked={joint}
              onChange={(event) => {
                bindingVersion.current++;
                setBinding(null);
                setProducer(null);
                setJoint(event.target.checked);
              }}
            />{" "}
            Compare model choice and output limits too
          </label>
          <p>
            {joint
              ? "Joint comparison: qualified model, output ceiling and actual retrieval settings. At most five alternatives; no prompt generator calls."
              : "Retrieval-only comparison: native model and output ceiling stay fixed."}
          </p>
          {!datasets.length && (
            <p>
              Import independently labeled questions in Integrations before
              preparing a comparison.
            </p>
          )}
          <label>
            Question dataset
            <select
              value={dataset}
              onChange={(e) => setDataset(e.target.value)}
              required
            >
              <option value="">Choose a dataset</option>
              {datasets.map((row) => (
                <option
                  value={row.id}
                  key={row.id}
                  disabled={!row.provenance?.independent_labels}
                >
                  {row.workflow} · {row.provenance?.case_count} questions ·{" "}
                  {row.id.slice(0, 8)} ·{" "}
                  {row.provenance?.independent_labels
                    ? "labels declared"
                    : "labels require review"}
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
            Corpus JSON
            <input
              type="file"
              accept=".json,application/json"
              onChange={(e) => {
                const file = e.currentTarget.files?.[0];
                e.currentTarget.value = "";
                void loadCorpus(file);
              }}
            />
          </label>
          {corpus && (
            <p role="status">
              {corpus.documents.length} documents selected in memory. Nothing
              uploaded yet.
            </p>
          )}
          <label>
            Source configuration inspection (optional)
            <input
              type="file"
              accept=".json,application/json"
              onChange={(e) => {
                const file = e.currentTarget.files?.[0];
                e.currentTarget.value = "";
                void loadBinding(file);
              }}
            />
          </label>
          <p>
            For a source diff, first run{" "}
            <code>
              cutback{" "}
              {joint
                ? "model-retrieval inspect-producer APP_ROOT app.py"
                : "retrieval inspect-config APP_ROOT retrieval.json"}{" "}
              --output ../inspection.json
            </code>
            . Enter the same baseline settings below. The exact source tree is
            checked again before a diff is prepared.
          </p>
          {binding && (
            <p role="status">Bound source configuration: {binding.file}</p>
          )}
          <div className="form-grid">
            <label>
              Provider
              <select
                name="provider"
                defaultValue={producer?.provider || "openai"}
                disabled={Boolean(producer)}
              >
                <option>openai</option>
                <option>anthropic</option>
              </select>
            </label>
            <label>
              Native model
              <input
                name="model"
                defaultValue={producer?.model || "gpt-4.1-mini"}
                readOnly={Boolean(producer)}
                required
                maxLength={200}
              />
            </label>
            <label>
              Application instruction (optional)
              <textarea
                name="instruction"
                maxLength={16000}
                readOnly={Boolean(producer)}
              />
            </label>
            <label>
              Native input ceiling
              <input
                name="inputLimit"
                type="number"
                min={1}
                max={100000}
                defaultValue={32768}
                required
              />
            </label>
            <label>
              Native output ceiling
              <input
                name="outputLimit"
                type="number"
                min={16}
                max={16384}
                defaultValue={producer?.max_output_tokens || 128}
                readOnly={Boolean(producer)}
                required
              />
            </label>
            <label>
              Context byte ceiling
              <input
                name="contextLimit"
                type="number"
                min={1024}
                max={48000}
                defaultValue={binding?.settings.max_context_bytes || 12000}
                readOnly={Boolean(binding)}
                required
              />
            </label>
            <label>
              Chunk characters
              <input
                name="chunk"
                type="number"
                min={128}
                max={4096}
                defaultValue={binding?.settings.chunk_chars || 512}
                readOnly={Boolean(binding)}
                required
              />
            </label>
            <label>
              Overlap characters
              <input
                name="overlap"
                type="number"
                min={0}
                max={1024}
                defaultValue={binding?.settings.overlap_chars || 0}
                readOnly={Boolean(binding)}
                required
              />
            </label>
            <label>
              Retrieved documents
              <input
                name="topk"
                type="number"
                min={1}
                max={12}
                defaultValue={binding?.settings.top_k || 1}
                readOnly={Boolean(binding)}
                required
              />
            </label>
            <label>
              Minimum exact-answer score
              <input
                name="minimum"
                type="number"
                min={0}
                max={1}
                step="0.01"
                defaultValue="0.9"
                required
              />
            </label>
            <label>
              Minimum independent questions
              <input
                name="samples"
                type="number"
                min={1}
                max={10000}
                defaultValue={30}
                required
              />
            </label>
            <label>
              Native API budget (USD)
              <input
                name="budget"
                defaultValue="1"
                pattern="[0-9]+(\.[0-9]+)?"
                required
              />
            </label>
            <label>
              Request ceiling
              <input
                name="requests"
                type="number"
                min={1}
                max={10000}
                defaultValue={200}
                required
              />
            </label>
            <label>
              Assumed CPU USD per hour (optional)
              <input
                name="cpu"
                placeholder="Unknown"
                pattern="[0-9]+(\.[0-9]+)?"
              />
            </label>
          </div>
          <p>
            Unknown CPU cost prevents an automatic economic selection. A
            declared rate covers measured CPU time only, including sample index
            construction; memory, idle infrastructure and full task cost remain
            unmeasured.
          </p>
          <label>
            <input name="retain" type="checkbox" /> Retain encrypted case
            outputs and retrieved passages under the existing policy
          </label>
          <label>
            <input type="checkbox" required /> I authorize this corpus upload
            under the selected dataset’s content rights and capture policy.
          </label>
          <button disabled={busy || !dataset || !corpus}>
            Prepare retrieval comparison
          </button>
        </form>
      )}
      {plan && (
        <section aria-label="Reviewed retrieval plan">
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
            Budget fit:{" "}
            {plan.fits_budget
              ? "Yes"
              : "No — prepare a sufficient envelope before approval"}
            .
          </p>
          <p>
            Plan hash: <code>{plan.plan_hash}</code>
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
                      approval_ref: "dashboard-retrieval-reviewed",
                    },
                  );
                  if (alive.current) setApproved(true);
                })
              }
            >
              Approve this exact retrieval budget
            </button>
          )}
          {canWrite && (
            <button
              disabled={busy || !approved || queueAttempted}
              onClick={() =>
                void act(async () => {
                  setQueueAttempted(true);
                  const value = await json(api, `${base}/experiments`, {
                    plan_id: plan.id,
                  });
                  await inspect(value.id);
                })
              }
            >
              Queue approved retrieval run
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
                    "No queued run was found. Inspect Experiments before deliberately creating another plan.",
                  );
                }
              })
            }
          >
            Reconcile plan execution
          </button>
          <p>
            Execution requires an operator-enabled worker. An interrupted or
            uncertain billable call is not automatically retried.
          </p>
        </section>
      )}
      {run && (
        <section aria-label="Retrieval run results">
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
            Refresh retrieval run
          </button>
          {results?.retrieval && (
            <div
              className="table-scroll"
              role="region"
              aria-label="Retrieval comparison"
              tabIndex={0}
            >
              <table>
                <thead>
                  <tr>
                    <th>Configuration</th>
                    <th>Native model / output ceiling</th>
                    <th>Total context bytes</th>
                    <th>Index CPU ms</th>
                    <th>Total query CPU ms</th>
                    <th>Modeled components</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(
                    results.retrieval.models as Record<string, Data>,
                  ).map(([key, value]) => (
                    <tr key={key}>
                      <th scope="row">{key}</th>
                      <td>
                        {results.models?.find(
                          (row: Data) => row.model.key === key,
                        )?.model.model || "Unknown"}{" "}
                        /{" "}
                        {results.models?.find(
                          (row: Data) => row.model.key === key,
                        )?.model.max_output_tokens ?? "Unknown"}
                      </td>
                      <td>{value.context_bytes}</td>
                      <td>{(value.index_cpu_ns / 1e6).toFixed(3)}</td>
                      <td>{(value.query_cpu_ns / 1e6).toFixed(3)}</td>
                      <td>{money(value.modeled_component_cost_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <ul>
            {results?.recommendations?.map((decision: Data) => (
              <li key={decision.candidate.key}>
                {decision.candidate.key}: answer quality{" "}
                {decision.retrieval_quality_assessment_passed
                  ? "passed"
                  : "requires review"}
                ; lower modeled component cost{" "}
                {decision.cheaper_on_sample === true
                  ? "yes"
                  : decision.cheaper_on_sample === false
                    ? "no"
                    : "unknown"}
                ; statistical gate{" "}
                {decision.statistical_gate_passed
                  ? "passed"
                  : "not established"}
                .
                {runJoint && (
                  <>
                    {" "}
                    Changed axes:{" "}
                    {decision.search_axes_changed?.join(", ") || "none"}.
                  </>
                )}
              </li>
            ))}
          </ul>
          <p>
            Synthetic evidence demonstrates this workflow only. Full task cost,
            customer savings and population quality are not inferred.
          </p>
          {canApprove && results?.case_outputs_retained && (
            <CaseEvidence key={run.id} api={api} base={base} runId={run.id} />
          )}
          {!confirmation && canWrite && run.status === "COMPLETED" && (
            <>
              {runJoint && (
                <label>
                  Confirmation candidate
                  <select
                    value={candidate}
                    onChange={(event) => setCandidate(event.target.value)}
                  >
                    <option value="">Choose an eligible candidate</option>
                    {results?.recommendations
                      ?.filter(
                        (row: Data) =>
                          row.joint_model_retrieval_confirmation_eligible,
                      )
                      .map((row: Data) => (
                        <option
                          key={row.candidate.key}
                          value={row.candidate.key}
                        >
                          {row.candidate.key}: {row.candidate.model},{" "}
                          {row.candidate.max_output_tokens} output tokens
                        </option>
                      ))}
                  </select>
                </label>
              )}
              <label>
                Confirmation API budget (USD)
                <input
                  value={confirmationBudget}
                  onChange={(event) =>
                    setConfirmationBudget(event.target.value)
                  }
                  inputMode="decimal"
                />
              </label>
              <label>
                Confirmation request ceiling
                <input
                  type="number"
                  min={1}
                  max={10000}
                  value={confirmationRequests}
                  onChange={(event) =>
                    setConfirmationRequests(Number(event.target.value))
                  }
                />
              </label>
              <button
                disabled={
                  busy ||
                  !results?.recommendations?.some((r: Data) =>
                    runJoint
                      ? r.joint_model_retrieval_confirmation_eligible &&
                        r.candidate.key === candidate
                      : r.retrieval_automation_eligible,
                  )
                }
                onClick={() =>
                  void act(async () => {
                    prepared(
                      await json(api, `${base}/${runRoute}/confirmation`, {
                        validation_run_id: run.id,
                        ...(runJoint ? { candidate } : {}),
                        budget: {
                          max_usd: confirmationBudget,
                          max_requests: confirmationRequests,
                        },
                      }),
                    );
                  })
                }
              >
                Prepare selected candidate confirmation
              </button>
            </>
          )}
          {confirmation && canApprove && run.status === "COMPLETED" && (
            <>
              <label>
                Proposal review purpose
                <input
                  value={purpose}
                  onChange={(e) => setPurpose(e.target.value)}
                  maxLength={200}
                  pattern="[A-Za-z0-9_.:/-]+"
                />
              </label>
              <button
                disabled={busy || !/^[A-Za-z0-9_.:/-]{1,200}$/.test(purpose)}
                onClick={() =>
                  void act(async () => {
                    const value = await json(
                      api,
                      `${base}/${runRoute}/${run.id}/proposal`,
                      { purpose },
                    );
                    if (alive.current) setProposal(value);
                  })
                }
              >
                Prepare source configuration proposal
              </button>
            </>
          )}
          {proposal && (
            <aside aria-label="Retrieval source proposal">
              <h3>Review the source change locally</h3>
              <p>
                {proposal.decision.replaceAll("_", " ")} · production approval
                remains required.
              </p>
              <p>
                Qualified files: <code>{proposal.binding.file}</code>
                {"producer" in proposal && (
                  <>
                    {" "}
                    and <code>{proposal.producer.file}</code>
                  </>
                )}
                . Run application checks against the exact base and proposed Git
                trees before review.
              </p>
              <button onClick={download}>Download retrieval proposal</button>
              <p>
                <code>
                  cutback{" "}
                  {"producer" in proposal ? "model-retrieval" : "retrieval"}{" "}
                  patch APP_ROOT retrieval-proposal.json --output
                  ../retrieval-change.json
                </code>
              </p>
            </aside>
          )}
        </section>
      )}
      <h2>Retrieval history</h2>
      <button
        disabled={busy || loading}
        onClick={() =>
          void act(async () => {
            const rows = await api(`${base}/experiments?limit=50`);
            if (alive.current) {
              setHistory(
                rows.filter((r: Data) => r.specification?.retrieval_execution),
              );
              setHistoryOffset(rows.length);
              setMoreHistory(rows.length === 50);
            }
          })
        }
      >
        Refresh retrieval history
      </button>
      {!history.length && !loading && (
        <p>No retrieval runs in the loaded history page.</p>
      )}
      <ul>
        {history.map((row) => (
          <li key={row.id}>
            <button
              disabled={busy}
              onClick={() => void act(() => inspect(row.id, true))}
            >
              {row.specification?.split === "test"
                ? "Confirmation"
                : "Validation"}{" "}
              · {row.status} · {row.id.slice(0, 8)}
            </button>
          </li>
        ))}
      </ul>
      {moreHistory && (
        <button
          disabled={busy}
          onClick={() =>
            void act(async () => {
              const rows = await api(
                `${base}/experiments?limit=50&offset=${historyOffset}`,
              );
              if (alive.current) {
                setHistory((old) => [
                  ...old,
                  ...rows.filter(
                    (r: Data) => r.specification?.retrieval_execution,
                  ),
                ]);
                setHistoryOffset((old) => old + rows.length);
                setMoreHistory(rows.length === 50);
              }
            })
          }
        >
          Load more experiment history
        </button>
      )}
    </section>
  );
}
