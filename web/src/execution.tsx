import React, { useEffect, useRef, useState } from "react";
import type { components } from "./api.generated";
import { type API, explain, money } from "./ui";
import { IsolatedToolEvidence } from "./isolated-tools";

type Calls = components["schemas"]["CallPage"];
type Episodes = components["schemas"]["EpisodePage"];

export function ExecutionEvidence({
  api,
  base,
  runId,
  canContain = false,
}: {
  api: API;
  base: string;
  runId: string;
  canContain?: boolean;
}) {
  const [calls, setCalls] = useState<Calls | null>(null);
  const [episodes, setEpisodes] = useState<Episodes | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  useEffect(() => {
    generation.current++;
    setCalls(null);
    setEpisodes(null);
    setError("");
    return () => {
      generation.current++;
    };
  }, [base, runId]);
  async function load(callAfter = 0, episodeAfter: string | null = null) {
    const request = ++generation.current;
    setBusy(true);
    setError("");
    try {
      const [c, e] = await Promise.all([
        api(`${base}/experiments/${runId}/calls?limit=50&after=${callAfter}`),
        api(
          `${base}/experiments/${runId}/episodes?limit=50${episodeAfter ? `&after=${episodeAfter}` : ""}`,
        ),
      ]);
      if (request === generation.current) {
        setCalls(c);
        setEpisodes(e);
      }
    } catch (e) {
      if (request === generation.current) setError(explain(e));
    } finally {
      if (request === generation.current) setBusy(false);
    }
  }
  return (
    <section aria-label="Agent execution and call receipts">
      <h3>Execution and call receipts</h3>
      <p>
        Inspect each reserved model call and the task it belonged to. Each
        episode identifies its fixture or isolated record contract. Production
        task success and total production task cost remain unverified.
      </p>
      <button disabled={busy} onClick={() => load()}>
        {busy
          ? "Loading execution receipts…"
          : calls
            ? "Refresh execution receipts"
            : "Load execution receipts"}
      </button>
      {error && <p role="alert">{error}</p>}
      {calls && (
        <>
          <p>
            {calls.requests_reserved} reserved requests ·{" "}
            {calls.known_cost_requests ?? "Unknown"} with known cost ·{" "}
            {money(calls.known_cost_subtotal_usd)} known subtotal. Cost
            coverage:{" "}
            {calls.cost_coverage == null
              ? "Unknown"
              : `${(100 * calls.cost_coverage).toFixed(1)}%`}
            .
          </p>
          <p className="hint">
            {calls.retained_receipts} call receipts retained. Cumulative counts
            survive retention; missing receipts cannot reconstruct latency
            distributions.
          </p>
          {!calls.items.length ? (
            <p>
              No call receipts on this page. Legacy runs have unknown per-call
              coverage.
            </p>
          ) : (
            <div className="table-scroll" tabIndex={0}>
              <table>
                <caption>Reserved native calls</caption>
                <thead>
                  <tr>
                    {[
                      "Call",
                      "Model",
                      "Purpose",
                      "State",
                      "Reserved",
                      "Known cost",
                      "Tokens in / out",
                    ].map((label) => (
                      <th key={label}>{label}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {calls.items.map((row) => (
                    <tr key={row.id}>
                      <th scope="row">{row.sequence}</th>
                      <td>
                        {row.model_key}
                        <br />
                        {row.provider}: {row.model}
                      </td>
                      <td>{row.purpose}</td>
                      <td>{row.state}</td>
                      <td>{money(row.reserved_usd)}</td>
                      <td>
                        {row.known_spent_usd == null
                          ? "Unknown"
                          : money(row.known_spent_usd)}
                      </td>
                      <td>
                        {row.input_tokens ?? "Unknown"} /{" "}
                        {row.output_tokens ?? "Unknown"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {calls.next_after != null && (
            <button disabled={busy} onClick={() => load(calls.next_after!)}>
              Next calls
            </button>
          )}
        </>
      )}
      {episodes && (
        <>
          <h4>Task episodes</h4>
          {!episodes.items.length ? (
            <p>
              No task episodes on this page. Single-call comparisons do not
              establish tool or final-state correctness.
            </p>
          ) : (
            <div className="table-scroll" tabIndex={0}>
              <table>
                <caption>Synthetic task episodes</caption>
                <thead>
                  <tr>
                    {[
                      "Model",
                      "Outcome",
                      "Contract checks",
                      "Requests",
                      "Known cost",
                    ].map((label) => (
                      <th key={label}>{label}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {episodes.items.map((row) => (
                    <tr key={row.id}>
                      <th scope="row">
                        {row.model_key}
                        <br />
                        Repeat {row.repeat_index + 1}
                      </th>
                      <td>{row.outcome ?? "Pending"}</td>
                      <td>
                        <p>{row.execution_version}</p>
                        {row.state}
                        <details>
                          <summary>Individual checks</summary>
                          <pre tabIndex={0}>
                            {JSON.stringify(row.summary, null, 2)}
                          </pre>
                        </details>
                      </td>
                      <td>
                        {row.requests_used} / {row.max_requests} ·{" "}
                        {row.known_cost_requests} priced
                      </td>
                      <td>{money(row.known_spent_usd)} known subtotal</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {episodes.next_after && (
            <button
              disabled={busy}
              onClick={() => load(0, episodes.next_after)}
            >
              Next episodes
            </button>
          )}
        </>
      )}
      <IsolatedToolEvidence
        api={api}
        base={base}
        runId={runId}
        canContain={canContain}
      />
    </section>
  );
}
