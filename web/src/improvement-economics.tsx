import React, { useEffect, useRef, useState } from "react";
import type { components } from "./api.generated";
import { type API, explain, money } from "./ui";

type Snapshot = components["schemas"]["TaskSnapshotBrief"];
type Receipt = components["schemas"]["ImprovementReceipt"];

export function ImprovementEconomics({
  api,
  base,
  environment,
  canWrite,
  newSnapshot,
}: {
  api: API;
  base: string;
  environment: string;
  canWrite: boolean;
  newSnapshot?: string;
}) {
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [before, setBefore] = useState("");
  const [after, setAfter] = useState("");
  const [result, setResult] = useState<Receipt | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [requestId, setRequestId] = useState("");
  const [allRuns, setAllRuns] = useState(false);
  const [allOther, setAllOther] = useState(false);
  const [linkAttested, setLinkAttested] = useState(false);
  const [history, setHistory] = useState<Receipt[]>([]);
  const [historyCursor, setHistoryCursor] = useState<string | null>(null);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const historyVersion = useRef(0);
  const version = useRef(0);
  const selectionVersion = useRef(0);
  const scopedHistory = history.filter(
    (item) => item.environment_id === environment,
  );
  async function loadHistory(afterId?: string) {
    const current = ++historyVersion.current;
    setHistoryBusy(true);
    setHistoryError("");
    try {
      const value = await api(
        `${base}/improvement-observations?environment_id=${environment}&limit=20${afterId ? `&after=${afterId}` : ""}`,
      );
      if (current === historyVersion.current) {
        setHistory((previous) =>
          afterId ? [...previous, ...value.items] : value.items,
        );
        setHistoryCursor(value.next_after);
      }
    } catch (e) {
      if (current === historyVersion.current) setHistoryError(explain(e));
    } finally {
      if (current === historyVersion.current) setHistoryBusy(false);
    }
  }
  async function load(afterId?: string) {
    const current = ++version.current;
    setError("");
    try {
      const value = await api(
        `${base}/task-cost-snapshots?environment_id=${environment}&limit=20${afterId ? `&after=${afterId}` : ""}`,
      );
      if (current === version.current) {
        setSnapshots((previous) =>
          afterId ? [...previous, ...value.items] : value.items,
        );
        setCursor(value.next_after);
      }
    } catch (e) {
      if (current === version.current) setError(explain(e));
    }
  }
  useEffect(() => {
    setBefore("");
    setAfter("");
    setResult(null);
    setRequestId("");
    setAllRuns(false);
    setAllOther(false);
    setLinkAttested(false);
    setHistory([]);
    setHistoryCursor(null);
    void loadHistory();
    void load();
    return () => {
      version.current++;
      selectionVersion.current++;
      historyVersion.current++;
    };
  }, [api, base, environment, newSnapshot]);
  function invalidate() {
    selectionVersion.current++;
    setResult(null);
    setError("");
    setBusy(false);
    setRequestId("");
  }
  function resetAttestations() {
    setAllRuns(false);
    setAllOther(false);
    setLinkAttested(false);
  }
  async function compare(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const current = ++selectionVersion.current;
    const id = requestId || crypto.randomUUID();
    setRequestId(id);
    setBusy(true);
    setError("");
    const value = (key: string) => String(form.get(key) || "");
    const otherCost = value("otherCost");
    const change = value("change");
    try {
      const receipt: Receipt = await api(`${base}/improvement-observations`, {
        method: "POST",
        body: JSON.stringify({
          observation_id: id,
          baseline_snapshot_id: before,
          candidate_snapshot_id: after,
          change_id: change || null,
          release_linkage_attested: form.has("linkAttested"),
          optimization_run_ids: value("runs")
            .split(",")
            .map((v) => v.trim())
            .filter(Boolean),
          assumptions: {
            all_optimization_runs_declared_complete: form.has("allRuns"),
            other_optimization_costs_declared_complete: form.has("allOther"),
            other_optimization_costs:
              otherCost === ""
                ? []
                : [
                    {
                      key: "declared-other-optimization",
                      kind: "other",
                      amount_usd: otherCost,
                    },
                  ],
            forecast_successful_tasks: value("volume")
              ? Number(value("volume"))
              : null,
            min_tasks_per_window: Number(value("minimum")),
            max_absolute_success_rate_loss: "0",
          },
        }),
      });
      if (current === selectionVersion.current) {
        setResult(receipt);
        void loadHistory();
      }
    } catch (e) {
      if (current === selectionVersion.current) setError(explain(e));
    } finally {
      if (current === selectionVersion.current) setBusy(false);
    }
  }
  return (
    <details className="editor">
      <summary>Compare frozen releases and optimization costs</summary>
      <p>
        Compare one workflow and service across distinct declared releases. This
        is descriptive evidence; release linkage and future volume are owner
        assertions.
      </p>
      <section aria-label="Saved economic comparisons">
        <h3>Saved economic comparisons</h3>
        <p>
          Read frozen comparisons recorded here or by your coding agent. Opening
          one makes no model call and changes no traffic.
        </p>
        <button disabled={historyBusy} onClick={() => loadHistory()}>
          {historyBusy
            ? "Loading saved comparisons…"
            : "Refresh saved comparisons"}
        </button>
        {historyError && <p role="alert">{historyError}</p>}
        {!scopedHistory.length && !historyBusy && !historyError && (
          <p>No saved comparisons in this environment.</p>
        )}
        <ul>
          {scopedHistory.map((item) => (
            <li key={item.id}>
              <button
                onClick={() => {
                  selectionVersion.current++;
                  setBusy(false);
                  setError("");
                  setRequestId("");
                  setResult(item);
                }}
              >
                Open comparison {item.evidence.baseline_release} →{" "}
                {item.evidence.candidate_release} · {item.id.slice(0, 8)}
              </button>
            </li>
          ))}
        </ul>
        {historyCursor && (
          <button
            disabled={historyBusy}
            onClick={() => loadHistory(historyCursor)}
          >
            Load more saved comparisons
          </button>
        )}
      </section>
      {!snapshots.length && (
        <p>
          Freeze task cost evidence for two releases first. Each receipt must
          contain a single declared workflow, service, and release.
        </p>
      )}
      {canWrite && snapshots.length > 0 && (
        <form
          aria-label="Compare frozen task windows"
          onSubmit={compare}
          onChange={invalidate}
        >
          <div className="toolbar">
            <label>
              Baseline frozen window
              <select
                required
                value={before}
                onChange={(e) => {
                  setBefore(e.target.value);
                  if (e.target.value === after) setAfter("");
                  resetAttestations();
                }}
              >
                <option value="">Choose reference</option>
                {snapshots.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.release || "Unfiltered releases"} · {s.id.slice(0, 8)} ·{" "}
                    {s.summary.submitted_tasks} tasks
                  </option>
                ))}
              </select>
            </label>
            <label>
              Candidate frozen window
              <select
                required
                value={after}
                onChange={(e) => {
                  setAfter(e.target.value);
                  resetAttestations();
                }}
              >
                <option value="">Choose candidate</option>
                {snapshots
                  .filter((s) => s.id !== before)
                  .map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.release || "Unfiltered releases"} · {s.id.slice(0, 8)}{" "}
                      · {s.summary.submitted_tasks} tasks
                    </option>
                  ))}
              </select>
            </label>
            <label>
              Optimization experiment run IDs
              <input
                name="runs"
                placeholder="Comma-separated run IDs"
                maxLength={800}
                onChange={() => setAllRuns(false)}
              />
            </label>
            <label>
              Other one-time optimization costs (USD)
              <input
                name="otherCost"
                type="number"
                min="0"
                step="0.000000000001"
                placeholder="Unknown"
                onChange={() => setAllOther(false)}
              />
            </label>
            <label>
              Forecast successful tasks
              <input
                name="volume"
                type="number"
                min="1"
                max="1000000000"
                step="1"
                placeholder="Unknown"
              />
            </label>
            <label>
              Minimum tasks per window
              <input
                name="minimum"
                type="number"
                min="1"
                max="10000"
                step="1"
                defaultValue="30"
                required
              />
            </label>
            <label>
              Source change ID (optional)
              <input
                name="change"
                placeholder="Reviewed source diff ID"
                maxLength={36}
                onChange={() => setLinkAttested(false)}
              />
            </label>
          </div>
          <label className="checkbox">
            <input
              type="checkbox"
              name="allRuns"
              checked={allRuns}
              onChange={(e) => setAllRuns(e.target.checked)}
            />
            I have listed all proposer, evaluation, judge, simulator, and other
            experiment runs for this improvement.
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              name="allOther"
              checked={allOther}
              onChange={(e) => setAllOther(e.target.checked)}
            />
            Other one-time costs are complete, including training, retrieval,
            compute, and engineering costs where applicable. Blank means
            explicitly none only when I select this.
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              name="linkAttested"
              checked={linkAttested}
              onChange={(e) => setLinkAttested(e.target.checked)}
            />
            If linking a source change, I attest that these declared application
            releases correspond to that change.
          </label>
          <button disabled={busy || !before || !after || before === after}>
            {busy ? "Comparing frozen evidence…" : "Record economic comparison"}
          </button>
        </form>
      )}
      {cursor && (
        <button onClick={() => load(cursor)}>Load more frozen windows</button>
      )}
      {error && <p role="alert">{error}</p>}
      {result && result.environment_id === environment && (
        <section aria-label="Improvement economic decision">
          <p>
            {result.evidence.baseline_release} →{" "}
            {result.evidence.candidate_release}
          </p>
          <h3>
            {result.evidence.report.decision ===
            "prepare_independent_confirmation"
              ? "Prepare independent confirmation"
              : "Abstain or reject"}
          </h3>
          {result.evidence.report.descriptive_outcome_non_regression ===
            false && (
            <p>
              Declared outcomes regressed. Review the change and rollback
              evidence before deciding what to do. This receipt does not perform
              a rollback.
            </p>
          )}
          {result.evidence.change_id && (
            <p>
              Reviewed source change: <code>{result.evidence.change_id}</code>
            </p>
          )}
          {result.evidence.report.reasons.length > 0 && (
            <ul>
              {result.evidence.report.reasons.map((reason) => (
                <li key={reason}>{explain(new Error(reason))}</li>
              ))}
            </ul>
          )}
          <dl className="stats">
            <div>
              <dt>Modeled cost improvement per successful task</dt>
              <dd>
                {money(
                  result.evidence.report.modeled_cost_per_success_delta_usd,
                )}
              </dd>
            </div>
            <div>
              <dt>Declared total optimization cost</dt>
              <dd>
                {money(result.evidence.report.declared_optimization_total_usd)}
              </dd>
            </div>
            <div>
              <dt>Forecast net modeled benefit</dt>
              <dd>
                {money(result.evidence.report.forecast_net_modeled_benefit_usd)}
              </dd>
            </div>
            <div>
              <dt>Forecast break-even successful tasks</dt>
              <dd>
                {result.evidence.report.forecast_break_even_successful_tasks ??
                  "Unknown"}
              </dd>
            </div>
          </dl>
          <p>
            Outcomes, population coverage, causation, billing, and future
            savings are not independently verified. This receipt authorizes no
            model calls or deployment.
          </p>
          <p className="hint">
            Immutable observation {result.id} · SHA-256{" "}
            <code>{result.evidence_sha256}</code>
          </p>
        </section>
      )}
    </details>
  );
}
