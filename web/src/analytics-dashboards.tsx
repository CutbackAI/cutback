import React, { useEffect, useRef, useState } from "react";
import type { components } from "./api.generated";
import { chartGeometry, exactValue } from "./analytics-chart";
import { Field, Select, explain, json, saveFile, type API } from "./ui";

type Detail = components["schemas"]["AnalyticsDetail"];
type Listing = components["schemas"]["AnalyticsPage"];
type Result = components["schemas"]["AnalyticsData"];
type Input = components["schemas"]["AnalyticsInput"];
type Panel = components["schemas"]["AnalyticsPanel"];
type PanelData = components["schemas"]["AnalyticsPanelData"];
type Draft = Required<Panel>;

const metrics: Record<Panel["metric"], string> = {
  observed_attempts: "Observed attempts",
  known_modeled_cost_subtotal_usd: "Known modeled cost subtotal",
  errors: "Attempts marked error",
  latency_p95_ms: "p95 duration",
  input_tokens_observed: "Reported input-token subtotal",
  output_tokens_observed: "Reported output-token subtotal",
};
const groups = ["day", "model", "provider", "workflow", "traffic"] as const;
const charts = ["stat", "bar", "line", "table"] as const;
const traffic = [
  "application",
  "evaluation",
  "judge",
  "optimization",
  "simulation",
  "unknown",
] as const;
const normalize = (
  panel: Pick<Panel, "key" | "metric"> & Partial<Omit<Panel, "key" | "metric">>,
): Draft => ({
  ...panel,
  chart: panel.chart || "bar",
  group_by: panel.group_by || "day",
  traffic_kind: panel.traffic_kind ?? null,
  workflow: panel.workflow ?? null,
});
const initialPanels = (): Draft[] => [
  normalize({ key: "traffic", metric: "observed_attempts", chart: "line" }),
  normalize({
    key: "cost",
    metric: "known_modeled_cost_subtotal_usd",
    group_by: "model",
  }),
  normalize({ key: "errors", metric: "errors", chart: "stat" }),
  normalize({ key: "duration", metric: "latency_p95_ms", chart: "line" }),
];

function MetricPanel({ panel }: { panel: PanelData }) {
  const plot = chartGeometry(panel.points);
  return (
    <article
      className="analytics-panel"
      aria-label={"Analytical panel " + panel.key}
    >
      <header>
        <h4>{metrics[panel.metric]}</h4>
        <span>{panel.key}</span>
      </header>
      <p className="analytics-value">
        {exactValue(panel.summary.value)} <small>{panel.unit}</small>
      </p>
      <p>{panel.metric_semantics}</p>
      {panel.chart === "line" && plot.known > 0 && (
        <figure>
          <svg
            viewBox="0 0 660 205"
            role="img"
            aria-label={
              metrics[panel.metric] +
              "; unknown days break the line; exact values follow"
            }
          >
            <line
              x1="30"
              x2="630"
              y1="170"
              y2="170"
              className="analytics-axis"
            />
            {plot.segments.map((segment, index) => (
              <g key={index}>
                {segment.length > 1 && (
                  <polyline
                    points={segment
                      .map((point) => point.x + "," + point.y)
                      .join(" ")}
                    className="analytics-line"
                  />
                )}
                {segment.map((point) => (
                  <circle
                    key={point.key}
                    cx={point.x}
                    cy={point.y}
                    r="3"
                    className="analytics-dot"
                  />
                ))}
              </g>
            ))}
          </svg>
          <div className="analytics-range">
            <span>{panel.points[0]?.key}</span>
            <span>{panel.points.at(-1)?.key}</span>
          </div>
          <figcaption>
            Relative scale; missing observations remain gaps.
          </figcaption>
        </figure>
      )}
      {panel.chart === "bar" && plot.known > 0 && (
        <div
          className="analytics-bars"
          role="img"
          aria-label={
            metrics[panel.metric] +
            " by " +
            panel.group_by +
            "; exact values follow"
          }
        >
          {panel.points.slice(0, 12).map((point) => (
            <div key={point.key}>
              <span title={point.key}>{point.key}</span>
              <span className="analytics-bar-track">
                <span
                  style={{
                    width:
                      exactValue(point.value) === "Unknown" || !plot.maximum
                        ? "0%"
                        : (Number(point.value) / plot.maximum) * 100 + "%",
                  }}
                />
              </span>
              <span>{exactValue(point.value)}</span>
            </div>
          ))}
        </div>
      )}
      {panel.summary.observed_attempts === 0 && (
        <p>
          No observations in this selection. This does not establish health.
        </p>
      )}
      {panel.metric === "latency_p95_ms" &&
        panel.summary.value === null &&
        panel.summary.observed_attempts > 0 && (
          <p>
            p95 is unavailable for this selection. Daily summaries cannot
            rebuild a missing combined quantile.
          </p>
        )}
      <p className="analytics-coverage">
        {panel.summary.observed_attempts} observed attempts ·{" "}
        {panel.summary.unpriced_attempts ?? "Unknown"} unpriced · population
        coverage unknown.
      </p>
      {panel.partial_days_excluded.length > 0 && (
        <p role="alert">
          Some retained partial days are excluded. This selection is incomplete.
        </p>
      )}
      {panel.points_truncated && (
        <p>
          Group selection is truncated. Narrow the workflow, period or traffic
          purpose.
        </p>
      )}
      <details open={panel.chart === "table"}>
        <summary>Exact values and coverage</summary>
        <div className="table-scroll" tabIndex={0}>
          <table>
            <caption>
              {panel.key}: {metrics[panel.metric]} ({panel.unit}) by{" "}
              {panel.group_by}
            </caption>
            <thead>
              <tr>
                <th>Group</th>
                <th>Value</th>
                <th>Observed attempts</th>
                <th>Unpriced</th>
                <th>Missing usage</th>
                <th>Timed attempts</th>
              </tr>
            </thead>
            <tbody>
              {panel.points.map((point) => (
                <tr key={point.key}>
                  <th scope="row">{point.key}</th>
                  <td>{exactValue(point.value)}</td>
                  <td>{point.observed_attempts}</td>
                  <td>{point.unpriced_attempts ?? "Unknown"}</td>
                  <td>{point.missing_usage_attempts ?? "Unknown"}</td>
                  <td>{point.timed_attempts ?? "Unknown"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </article>
  );
}

export function AnalyticsDashboards({
  api,
  base,
  editor,
}: {
  api: API;
  base: string;
  editor: boolean;
}) {
  const [listing, setListing] = useState<Listing>({ items: [] });
  const [selected, setSelected] = useState<Detail | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [editing, setEditing] = useState<Detail | null>(null);
  const [form, setForm] = useState(false);
  const [name, setName] = useState("application-health");
  const [environment, setEnvironment] = useState("staging");
  const [windowDays, setWindowDays] = useState(7);
  const [panels, setPanels] = useState<Draft[]>(initialPanels);
  const [pending, setPending] = useState<Input | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const scopeGeneration = useRef(0);
  const newId = useRef(crypto.randomUUID());

  useEffect(() => {
    scopeGeneration.current++;
    const epoch = ++generation.current;
    const controller = new AbortController();
    setListing({ items: [] });
    setSelected(null);
    setResult(null);
    setEditing(null);
    setForm(false);
    setPending(null);
    setError("");
    setBusy(false);
    api(base + "/analytics-dashboards", { signal: controller.signal })
      .then((value) => {
        if (epoch === generation.current) setListing(value);
      })
      .catch((cause) => {
        if (!controller.signal.aborted && epoch === generation.current)
          setError(explain(cause));
      });
    return () => {
      scopeGeneration.current++;
      generation.current++;
      controller.abort();
    };
  }, [api, base]);

  async function act(callback: () => Promise<void>) {
    const epoch = scopeGeneration.current;
    setBusy(true);
    setError("");
    try {
      await callback();
    } catch (cause) {
      if (epoch === scopeGeneration.current) setError(explain(cause));
    } finally {
      if (epoch === scopeGeneration.current) setBusy(false);
    }
  }
  async function open(id: string) {
    const epoch = ++generation.current;
    if (!pending) {
      setForm(false);
      setEditing(null);
    }
    setSelected(null);
    setResult(null);
    const detail: Detail = await api(base + "/analytics-dashboards/" + id);
    if (epoch !== generation.current) return;
    setSelected(detail);
    const data: Result = await api(
      base +
        "/analytics-dashboards/" +
        id +
        "/data?expected_revision=" +
        detail.revision,
    );
    if (epoch === generation.current) setResult(data);
  }
  function edit(value: Detail | null) {
    if (pending) return;
    if (value === null) newId.current = crypto.randomUUID();
    setEditing(value);
    setForm(true);
    setError("");
    setName(value?.key || "application-health");
    setEnvironment(value?.environment || "staging");
    setWindowDays(value?.window_days || 7);
    setPanels(value?.panels.map(normalize) || initialPanels());
  }
  function update(index: number, value: Partial<Draft>) {
    setPanels((items) =>
      items.map((panel, position) =>
        position === index ? { ...panel, ...value } : panel,
      ),
    );
  }
  async function save(body: Input) {
    const epoch = generation.current;
    setPending(body);
    let saved: Detail;
    try {
      saved = await json(api, base + "/analytics-dashboards", body);
    } catch (cause) {
      const code = (cause as Error)?.message;
      if (
        epoch === generation.current &&
        [
          "ANALYTICS_REVISION_CONFLICT",
          "ANALYTICS_IDENTITY_CONFLICT",
          "ANALYTICS_DASHBOARD_WAS_DELETED",
          "ANALYTICS_ENVIRONMENT_NOT_CONFIGURED",
          "SAVED_VIEW_TYPE_CONFLICT",
          "SAVED_VIEW_QUOTA_EXCEEDED",
          "SCOPE_DENIED",
        ].includes(code)
      )
        setPending(null);
      throw cause;
    }
    if (epoch !== generation.current) return;
    setPending(null);
    setForm(false);
    setEditing(null);
    newId.current = crypto.randomUUID();
    setListing((old) => ({
      ...old,
      items: [saved, ...old.items.filter((item) => item.id !== saved.id)],
    }));
    await open(saved.id);
  }
  return (
    <section
      className="analytics-workspace"
      aria-labelledby="analytics-heading"
    >
      <div className="analytics-heading">
        <div>
          <h2 id="analytics-heading">Shared analytical dashboards</h2>
          <p>
            Save the cost, traffic, error and duration charts your team uses.
            These views read metadata and run no inference.
          </p>
        </div>
        {editor && (
          <button disabled={busy || !!pending} onClick={() => edit(null)}>
            Create analytical dashboard
          </button>
        )}
      </div>
      {error && <p role="alert">{error}</p>}
      {!listing.items.length && (
        <p>
          No saved analytical dashboards.{" "}
          {editor
            ? "Start with the application-health template."
            : "A project editor can create a shared view."}
        </p>
      )}
      <div className="analytics-picker">
        {listing.items.map((item) => (
          <button
            key={item.id}
            disabled={busy || !!pending}
            aria-pressed={selected?.id === item.id}
            onClick={() => act(() => open(item.id))}
          >
            {item.key} · {item.environment}
          </button>
        ))}
      </div>
      {listing.next_cursor && (
        <button
          disabled={busy}
          onClick={() =>
            act(async () => {
              const epoch = generation.current;
              const next: Listing = await api(
                base + "/analytics-dashboards?after=" + listing.next_cursor,
              );
              if (epoch === generation.current)
                setListing((old) => ({
                  ...next,
                  items: Array.from(
                    new Map(
                      [...old.items, ...next.items].map((item) => [
                        item.id,
                        item,
                      ]),
                    ).values(),
                  ),
                }));
            })
          }
        >
          Load more analytical dashboards
        </button>
      )}
      {editor && form && (
        <form
          className="analytics-editor"
          aria-label={
            editing
              ? "Edit analytical dashboard"
              : "Create analytical dashboard"
          }
          onSubmit={(event) => {
            event.preventDefault();
            const body: Input = pending || {
              dashboard_id: editing?.id || newId.current,
              key: name,
              expected_revision: editing?.revision || 0,
              environment,
              window_days: windowDays,
              panels,
            };
            act(() => save(body));
          }}
        >
          <h3>
            {editing
              ? "Edit analytical dashboard"
              : "Create shared analytical view"}
          </h3>
          <fieldset disabled={busy || !!pending}>
            <legend>Dashboard selection</legend>
            <Field
              label="Analytical dashboard name"
              name="analytics-key"
              value={name}
              onChange={(event) => setName(event.target.value)}
              pattern="[A-Za-z0-9_.:-]+"
              maxLength={100}
              required
              disabled={!!editing}
            />
            <Field
              label="Analytical environment"
              name="analytics-environment"
              value={environment}
              onChange={(event) => setEnvironment(event.target.value)}
              maxLength={100}
              required
            />
            <Select
              label="Analytical period"
              name="analytics-period"
              value={windowDays}
              onChange={(event) => setWindowDays(Number(event.target.value))}
            >
              {[1, 7, 30, 90, 365].map((days) => (
                <option key={days} value={days}>
                  {days} UTC {days === 1 ? "day" : "days"}, including today
                </option>
              ))}
            </Select>
            {panels.map((panel, index) => (
              <fieldset className="analytics-panel-editor" key={index}>
                <legend>Chart {index + 1}</legend>
                <Field
                  label="Chart name"
                  name={"chart-name-" + index}
                  required
                  maxLength={100}
                  value={panel.key}
                  onChange={(event) =>
                    update(index, { key: event.target.value })
                  }
                />
                <Select
                  label="Metric"
                  name={"chart-metric-" + index}
                  value={panel.metric}
                  onChange={(event) =>
                    update(index, {
                      metric: event.target.value as Panel["metric"],
                    })
                  }
                >
                  {Object.entries(metrics).map(([key, label]) => (
                    <option key={key} value={key}>
                      {label}
                    </option>
                  ))}
                </Select>
                <Select
                  label="Chart type"
                  name={"chart-type-" + index}
                  value={panel.chart}
                  onChange={(event) =>
                    update(index, {
                      chart: event.target.value as Draft["chart"],
                      ...(event.target.value === "line"
                        ? { group_by: "day" }
                        : {}),
                    })
                  }
                >
                  {charts.map((value) => (
                    <option key={value}>{value}</option>
                  ))}
                </Select>
                <Select
                  label="Group by"
                  name={"chart-group-" + index}
                  value={panel.group_by}
                  disabled={panel.chart === "line"}
                  onChange={(event) =>
                    update(index, {
                      group_by: event.target.value as Draft["group_by"],
                    })
                  }
                >
                  {groups.map((value) => (
                    <option key={value}>{value}</option>
                  ))}
                </Select>
                <Select
                  label="Traffic purpose"
                  name={"chart-traffic-" + index}
                  value={panel.traffic_kind || "all"}
                  onChange={(event) =>
                    update(index, {
                      traffic_kind:
                        event.target.value === "all"
                          ? null
                          : (event.target.value as Draft["traffic_kind"]),
                    })
                  }
                >
                  <option value="all">All observed traffic</option>
                  {traffic.map((value) => (
                    <option key={value}>{value}</option>
                  ))}
                </Select>
                <Field
                  label="Workflow filter (optional)"
                  name={"chart-workflow-" + index}
                  maxLength={200}
                  value={panel.workflow || ""}
                  onChange={(event) =>
                    update(index, { workflow: event.target.value || null })
                  }
                />
                <button
                  type="button"
                  disabled={panels.length === 1}
                  onClick={() =>
                    setPanels((items) =>
                      items.filter((_, position) => position !== index),
                    )
                  }
                >
                  Remove chart {index + 1}
                </button>
              </fieldset>
            ))}
            <button
              type="button"
              disabled={panels.length >= 8}
              onClick={() =>
                setPanels((items) => {
                  let number = 1;
                  while (items.some((item) => item.key === "chart-" + number))
                    number++;
                  return [
                    ...items,
                    normalize({
                      key: "chart-" + number,
                      metric: "observed_attempts",
                    }),
                  ];
                })
              }
            >
              Add chart
            </button>
          </fieldset>
          {pending && (
            <p role="status">
              Save outcome is unconfirmed. Retry the same saved request or
              inspect its current version before changing it.
            </p>
          )}
          <div className="analytics-actions">
            <button type="submit" disabled={busy}>
              {pending
                ? "Retry same analytical save"
                : "Save analytical dashboard"}
            </button>
            {pending && (
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  act(async () => {
                    const identifier = pending.dashboard_id,
                      epoch = scopeGeneration.current;
                    await open(identifier);
                    if (epoch === scopeGeneration.current) {
                      setPending(null);
                      setForm(false);
                    }
                  })
                }
              >
                Inspect saved analytical version
              </button>
            )}
            {!pending && (
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setForm(false);
                  setEditing(null);
                }}
              >
                Cancel analytical edit
              </button>
            )}
          </div>
        </form>
      )}
      {selected && (
        <div className="analytics-selection">
          <h3>{selected.key}</h3>
          <p>
            {selected.environment} · revision {selected.revision} · population
            coverage unknown. Modeled cost is not an invoice.
          </p>
          <div className="analytics-actions">
            <button
              disabled={busy}
              onClick={() => act(() => open(selected.id))}
            >
              Refresh analytical data
            </button>
            {editor && (
              <>
                <button
                  disabled={busy || !!pending}
                  onClick={() => edit(selected)}
                >
                  Edit analytical dashboard
                </button>
                <button
                  disabled={busy || !!pending}
                  onClick={() =>
                    act(async () => {
                      const epoch = generation.current;
                      await api(
                        base +
                          "/analytics-dashboards/" +
                          selected.id +
                          "?expected_revision=" +
                          selected.revision,
                        { method: "DELETE" },
                      );
                      if (epoch !== generation.current) return;
                      setSelected(null);
                      setResult(null);
                      setListing((old) => ({
                        ...old,
                        items: old.items.filter(
                          (item) => item.id !== selected.id,
                        ),
                      }));
                    })
                  }
                >
                  Delete analytical dashboard
                </button>
              </>
            )}
            {result && (
              <button
                onClick={() => saveFile(result, "cutback-analytics.json")}
              >
                Download analytical data
              </button>
            )}
          </div>
          {!result ? (
            <p role="status">
              Analytical data is unavailable or loading. No previous result is
              substituted.
            </p>
          ) : (
            <>
              <p>
                Observed window: {result.starts_at} → {result.ends_at} (UTC).
              </p>
              <div className="analytics-grid">
                {result.panels.map((panel) => (
                  <MetricPanel key={panel.key} panel={panel} />
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}
