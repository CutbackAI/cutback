import React, { useEffect, useRef, useState } from "react";
import type { components } from "./api.generated";
import { type API, explain, money } from "./ui";
import { ImprovementEconomics } from "./improvement-economics";

type Window = components["schemas"]["TaskWindow"];
type Snapshot = components["schemas"]["TaskSnapshotReceipt"];

export function TaskEconomics({
  api,
  project,
  canWrite,
}: {
  api: API;
  project: string;
  canWrite: boolean;
}) {
  const [environments, setEnvironments] = useState<
    { id: string; name: string }[]
  >([]);
  const [environment, setEnvironment] = useState("");
  const [report, setReport] = useState<Window | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [days, setDays] = useState("7");
  const [workflow, setWorkflow] = useState("");
  const [release, setRelease] = useState("");
  const [expected, setExpected] = useState("");
  const [query, setQuery] = useState<URLSearchParams | null>(null);
  const [freezeRequest, setFreezeRequest] = useState<string | null>(null);
  const generation = useRef(0);
  const metadataGeneration = useRef(0);
  const base = `/projects/${project}`;
  useEffect(() => {
    const request = ++metadataGeneration.current;
    generation.current++;
    setReport(null);
    setSnapshot(null);
    setQuery(null);
    setError("");
    api(`${base}/environments`)
      .then((value) => {
        if (request === metadataGeneration.current) {
          setEnvironments(value.items);
          setEnvironment(value.items[0]?.id || "");
        }
      })
      .catch((e) => {
        if (request === metadataGeneration.current) setError(explain(e));
      });
    return () => {
      metadataGeneration.current++;
      generation.current++;
    };
  }, [api, base]);
  function invalidate() {
    generation.current++;
    setReport(null);
    setSnapshot(null);
    setQuery(null);
    setFreezeRequest(null);
    setBusy(false);
    setError("");
  }
  async function load(event: React.FormEvent) {
    event.preventDefault();
    const request = ++generation.current;
    setBusy(true);
    setError("");
    setSnapshot(null);
    setReport(null);
    setFreezeRequest(null);
    const end = new Date();
    const params = new URLSearchParams({
      environment_id: environment,
      start: new Date(end.getTime() - Number(days) * 86400000).toISOString(),
      end: end.toISOString(),
    });
    if (workflow) params.set("workflow", workflow);
    if (release) params.set("release", release);
    if (expected !== "") params.set("expected_tasks", expected);
    try {
      const value: Window = await api(`${base}/task-economics?${params}`);
      if (request === generation.current) {
        setReport(value);
        setQuery(params);
      }
    } catch (e) {
      if (request === generation.current) setError(explain(e));
    } finally {
      if (request === generation.current) setBusy(false);
    }
  }
  async function freeze() {
    if (!report || !query) return;
    const request = ++generation.current;
    const id = freezeRequest || crypto.randomUUID();
    setFreezeRequest(id);
    setBusy(true);
    setError("");
    try {
      const value: Snapshot = await api(
        `${base}/task-cost-snapshots?environment_id=${environment}`,
        {
          method: "POST",
          body: JSON.stringify({
            snapshot_id: id,
            start: query.get("start"),
            end: query.get("end"),
            workflow: query.get("workflow"),
            release: query.get("release"),
            expected_tasks: query.has("expected_tasks")
              ? Number(query.get("expected_tasks"))
              : null,
            as_of: report.declarations_as_of,
          }),
        },
      );
      if (request === generation.current) {
        setSnapshot(value);
        setReport(value.report);
      }
    } catch (e) {
      if (request === generation.current) setError(explain(e));
    } finally {
      if (request === generation.current) setBusy(false);
    }
  }
  return (
    <section aria-label="Application task economics">
      <p>
        Join explicit application outcomes to retained native calls and declared
        tool, retrieval, and compute costs. Failed tasks count toward the cost
        of each successful task.
      </p>
      <form className="editor" onSubmit={load}>
        <div className="toolbar">
          <label>
            Task environment
            <select
              value={environment}
              onChange={(e) => {
                invalidate();
                setEnvironment(e.target.value);
              }}
            >
              {environments.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Task period
            <select
              value={days}
              onChange={(e) => {
                invalidate();
                setDays(e.target.value);
              }}
            >
              {["1", "7", "30"].map((d) => (
                <option key={d} value={d}>
                  {d} days
                </option>
              ))}
            </select>
          </label>
          <label>
            Task workflow
            <input
              value={workflow}
              onChange={(e) => {
                invalidate();
                setWorkflow(e.target.value);
              }}
              placeholder="All workflows"
              maxLength={200}
            />
          </label>
          <label>
            Application release
            <input
              value={release}
              onChange={(e) => {
                invalidate();
                setRelease(e.target.value);
              }}
              placeholder="All releases"
              maxLength={200}
            />
          </label>
          <label>
            Expected tasks in window
            <input
              type="number"
              min="0"
              max="1000000"
              step="1"
              value={expected}
              onChange={(e) => {
                invalidate();
                setExpected(e.target.value);
              }}
              placeholder="Unknown"
            />
          </label>
        </div>
        <p className="hint">
          Supply an independent application task count to check the submitted
          denominator. Leaving it blank preserves unknown window coverage.
        </p>
        <button disabled={busy || !environment}>
          {busy ? "Reading task evidence…" : "Read task economics"}
        </button>
      </form>
      {error && <p role="alert">{error}</p>}
      {report && (
        <>
          <h2>Cost and outcome coverage</h2>
          <dl className="stats">
            <div>
              <dt>Submitted tasks</dt>
              <dd>{report.summary.submitted_tasks}</dd>
            </div>
            <div>
              <dt>Declared successes / failures</dt>
              <dd>
                {report.summary.declared_successes} /{" "}
                {report.summary.declared_failures}
              </dd>
            </div>
            <div>
              <dt>Unknown or error outcomes</dt>
              <dd>{report.summary.unknown_or_error_outcomes}</dd>
            </div>
            <div>
              <dt>Tasks with complete declared cost</dt>
              <dd>{report.summary.declared_cost_complete_tasks}</dd>
            </div>
            <div>
              <dt>Sample cost per successful task</dt>
              <dd>
                {money(report.summary.declared_sample_cost_per_success_usd)}
              </dd>
            </div>
            <div>
              <dt>Window cost per successful task</dt>
              <dd>
                {money(report.summary.declared_window_cost_per_success_usd)}
              </dd>
            </div>
            <div>
              <dt>Known native spend on failed tasks</dt>
              <dd>
                {money(
                  report.summary.known_failed_task_native_modeled_subtotal_usd,
                )}
              </dd>
            </div>
          </dl>
          <p className="notice">
            Outcomes and completeness are application assertions. Rates are
            modeled; billing and population coverage are unverified. These
            results do not establish production savings.
          </p>
          {!report.items.length ? (
            <p>
              No submitted tasks in this window. Add explicit business outcomes
              using the independent task-economics example.
            </p>
          ) : (
            <div className="table-scroll" tabIndex={0}>
              <table>
                <caption>Application tasks and cost coverage</caption>
                <thead>
                  <tr>
                    {[
                      "Workflow / release",
                      "Declared outcome",
                      "Native attempts priced / observed / expected",
                      "Known native cost",
                      "Unknown other components",
                      "Complete declared task cost",
                      "Trace integrity",
                    ].map((x) => (
                      <th key={x}>{x}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {report.items.map((item) => (
                    <tr key={item.id}>
                      <th scope="row">
                        {item.declaration.workflow}
                        <br />
                        {item.declaration.release || "Unknown release"}
                        <br />
                        <small>
                          revision {item.revision} ·{" "}
                          {item.declaration.task_id.slice(0, 8)}
                        </small>
                      </th>
                      <td>
                        {item.declaration.outcome}
                        <br />
                        <small>{item.declaration.basis}</small>
                      </td>
                      <td>
                        {item.economics.priced_native_attempts} /{" "}
                        {item.economics.observed_native_attempts} /{" "}
                        {item.economics.expected_native_attempts ?? "Unknown"}
                      </td>
                      <td>
                        {money(
                          item.economics.known_native_modeled_subtotal_usd,
                        )}
                      </td>
                      <td>
                        {item.economics.unknown_declared_components}
                        {!item.declaration.other_costs_declared_complete &&
                          " · completeness unknown"}
                      </td>
                      <td>
                        {money(item.economics.declared_modeled_total_usd)}
                      </td>
                      <td>
                        {item.trace_integrity
                          .filter((x) => x !== "capture_coverage_unknown")
                          .join(", ")
                          .replaceAll("_", " ") || "Retained graph consistent"}
                        {item.span_issue_count > 0 &&
                          ` · ${item.span_issue_count} span issues`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="hint">
            Calculated {new Date(report.calculated_at).toLocaleString()}. Live
            reports can change as late telemetry arrives or retention expires.
          </p>
          {canWrite && (
            <button disabled={busy || !!snapshot} onClick={freeze}>
              Freeze task cost evidence
            </button>
          )}
          {snapshot && (
            <p role="status">
              Frozen receipt {snapshot.id}. Evidence SHA-256:{" "}
              <code>{snapshot.evidence_sha256}</code>. Later arrivals do not
              change this receipt.
            </p>
          )}
        </>
      )}
      {environment && (
        <ImprovementEconomics
          key={environment}
          api={api}
          base={base}
          environment={environment}
          canWrite={canWrite}
          newSnapshot={snapshot?.id}
        />
      )}
    </section>
  );
}
