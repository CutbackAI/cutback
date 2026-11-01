import React, { useEffect, useRef, useState } from "react";
import type { components } from "./api.generated";
import {
  Check,
  Editor,
  Field,
  Form,
  Select,
  explain,
  json,
  type API,
  type Data,
} from "./ui";
import { Investigation } from "./traces";

type Summary = components["schemas"]["DashboardSummary"];
type Detail = components["schemas"]["DashboardDetail"];
type Evidence = components["schemas"]["DashboardEvidencePage"];
type Listing = components["schemas"]["DashboardPage"];
type PanelState = { data?: Evidence; error?: string };

export function SharedDashboards({
  api,
  base,
  editor,
  views,
  reload,
  onIncident,
}: {
  api: API;
  base: string;
  editor: boolean;
  views: Data[];
  reload: () => Promise<void>;
  onIncident: (id: string) => Promise<void>;
}) {
  const [listing, setListing] = useState<Listing>({ items: [] });
  const [selected, setSelected] = useState<Detail | null>(null);
  const [editing, setEditing] = useState<Detail | null>(null);
  const [panels, setPanels] = useState<Record<string, PanelState>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [environment, setEnvironment] = useState("staging");
  const [chosen, setChosen] = useState<string[]>([]);
  const [trace, setTrace] = useState("");
  const generation = useRef(0);
  const newId = useRef(crypto.randomUUID());
  const traceViewId = useRef(crypto.randomUUID());

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    api(base + "/dashboards", { signal: controller.signal })
      .then((value) => {
        if (active) setListing(value);
      })
      .catch((cause) => {
        if (active) setError(explain(cause));
      });
    return () => {
      active = false;
      generation.current++;
      controller.abort();
    };
  }, [api, base]);

  async function act(callback: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await callback();
    } catch (cause) {
      setError(explain(cause));
    } finally {
      setBusy(false);
    }
  }

  async function open(id: string) {
    const request = ++generation.current;
    setSelected(null);
    setPanels({});
    setTrace("");
    const detail: Detail = await api(
      base + "/dashboards/" + encodeURIComponent(id),
    );
    if (request !== generation.current) return;
    setSelected(detail);
    await Promise.all(
      detail.panels.map(async (panel) => {
        try {
          const data: Evidence = await api(
            base +
              "/dashboards/" +
              encodeURIComponent(id) +
              "/panels/" +
              panel.view_id +
              "?expected_revision=" +
              detail.revision,
          );
          if (request === generation.current)
            setPanels((prior) => ({ ...prior, [panel.view_id]: { data } }));
        } catch (cause) {
          if (request === generation.current)
            setPanels((prior) => ({
              ...prior,
              [panel.view_id]: { error: explain(cause) },
            }));
        }
      }),
    );
  }

  const available = views.filter(
    (row) => row.specification.environment === environment,
  );
  const missing = chosen.filter(
    (id) => !available.some((row) => row.id === id),
  );
  const environments = Array.from(
    new Set([
      "staging",
      ...views.map((row) => String(row.specification.environment)),
    ]),
  );

  return (
    <section aria-label="Shared team dashboards" className="shared-dashboards">
      <h2>Shared team dashboards</h2>
      <p>
        Pin up to eight saved metadata views for one environment. A later view
        edit does not change the dashboard's filters. Retained samples do not
        establish population coverage, task success or latency quantiles.
      </p>
      {error && <p role="alert">{error}</p>}
      <div className="actions">
        <button disabled={busy} onClick={() => act(reload)}>
          Refresh saved views
        </button>
        {listing.items.map((row: Summary) => (
          <button
            key={row.id}
            disabled={busy}
            onClick={() => act(() => open(row.id))}
          >
            Open shared dashboard {row.key}
          </button>
        ))}
        {!listing.items.length && (
          <p>
            No shared dashboards yet. Save a trace or incident view, then choose
            its panels below.
          </p>
        )}
        {listing.next_cursor && (
          <button
            disabled={busy}
            onClick={() =>
              act(async () => {
                const page: Listing = await api(
                  base + "/dashboards?after=" + listing.next_cursor,
                );
                setListing((prior) => ({
                  items: [...prior.items, ...page.items],
                  next_cursor: page.next_cursor,
                }));
              })
            }
          >
            Load more shared dashboards
          </button>
        )}
      </div>
      {selected && (
        <section aria-label={"Shared dashboard " + selected.key}>
          <h3>
            {selected.key} · revision {selected.revision}
          </h3>
          <p>
            Environment: {selected.environment}. Definition:{" "}
            <code>{selected.definition_sha256}</code>
          </p>
          <button disabled={busy} onClick={() => act(() => open(selected.id))}>
            Refresh dashboard
          </button>
          {editor && (
            <>
              <button
                disabled={busy}
                onClick={() => {
                  setEditing(selected);
                  setEnvironment(selected.environment);
                  setChosen(selected.panels.map((panel) => panel.view_id));
                }}
              >
                Edit dashboard composition
              </button>
              <button
                disabled={busy}
                onClick={() =>
                  act(async () => {
                    await api(
                      base +
                        "/dashboards/" +
                        selected.id +
                        "?expected_revision=" +
                        selected.revision,
                      { method: "DELETE" },
                    );
                    generation.current++;
                    setSelected(null);
                    setPanels({});
                    setTrace("");
                    if (editing?.id === selected.id) {
                      setEditing(null);
                      setChosen([]);
                    }
                    setListing(await api(base + "/dashboards"));
                  })
                }
              >
                Delete shared dashboard {selected.key}
              </button>
            </>
          )}
          <div className="shared-dashboard-panels">
            {selected.panels.map((panel) => {
              const state = panels[panel.view_id];
              return (
                <article
                  key={panel.view_id}
                  aria-label={"Dashboard panel " + panel.key}
                >
                  <h4>{panel.key}</h4>
                  <p>
                    Pinned view revision {panel.revision} · source{" "}
                    {panel.source_state}.
                  </p>
                  {panel.source_state !== "current" && (
                    <p role="status">
                      The source view{" "}
                      {panel.source_state === "deleted"
                        ? "was deleted"
                        : "changed"}
                      . This panel still uses its pinned filters. An editor can
                      explicitly review and update the composition.
                    </p>
                  )}
                  <p>
                    Filters:{" "}
                    {Object.entries(panel.filter)
                      .filter(([, value]) => value !== null)
                      .map(([key, value]) => key + "=" + value)
                      .join(", ") || "all retained records"}
                    {panel.incident_state
                      ? "; state=" + panel.incident_state
                      : ""}
                    .
                  </p>
                  {state?.error ? (
                    <p role="alert">Panel unavailable: {state.error}</p>
                  ) : !state?.data ? (
                    <p role="status">Loading panel…</p>
                  ) : (
                    <>
                      <p>
                        {state.data.selection_basis ===
                        "matching_retained_spans"
                          ? "Retained spans matching these filters"
                          : "Incidents whose rule filters match this selection"}
                        . Population coverage: unknown.
                      </p>
                      {!state.data.items.length && (
                        <p>
                          No matching retained records. This does not establish
                          health.
                        </p>
                      )}
                      <ul>
                        {state.data.items.map((row) => (
                          <li key={row.id}>
                            {row.kind === "span" ? (
                              <>
                                <span>
                                  {row.workflow || "unknown workflow"} ·{" "}
                                  {row.span_kind || "unknown kind"} · execution{" "}
                                  {row.status || "unknown"}
                                </span>{" "}
                                <button
                                  onClick={() =>
                                    setTrace(row.trace_record_id || "")
                                  }
                                >
                                  Inspect trace{" "}
                                  {row.trace_record_id?.slice(0, 8)}
                                </button>
                              </>
                            ) : (
                              <>
                                <span>
                                  Incident {row.incident_state} · owner{" "}
                                  {row.owner || "unassigned"}
                                </span>{" "}
                                <button
                                  disabled={busy}
                                  onClick={() => act(() => onIncident(row.id))}
                                >
                                  Inspect incident {row.id.slice(0, 8)}
                                </button>
                              </>
                            )}
                          </li>
                        ))}
                      </ul>
                      {state.data.next_cursor && (
                        <button
                          disabled={busy}
                          onClick={() =>
                            act(async () => {
                              const request = generation.current;
                              const next: Evidence = await api(
                                base +
                                  "/dashboards/" +
                                  selected.id +
                                  "/panels/" +
                                  panel.view_id +
                                  "?expected_revision=" +
                                  selected.revision +
                                  "&after=" +
                                  state.data!.next_cursor,
                              );
                              if (request === generation.current)
                                setPanels((prior) => ({
                                  ...prior,
                                  [panel.view_id]: {
                                    data: {
                                      ...next,
                                      items: [
                                        ...state.data!.items,
                                        ...next.items,
                                      ],
                                    },
                                  },
                                }));
                            })
                          }
                        >
                          Load more panel records for {panel.key}
                        </button>
                      )}
                    </>
                  )}
                </article>
              );
            })}
          </div>
          {trace && (
            <Investigation
              key={trace}
              api={api}
              base={base}
              id={trace}
              allowContent={false}
            />
          )}
        </section>
      )}
      {editor && (
        <>
          <Editor title="Compose a shared dashboard" open={Boolean(editing)}>
            <Form
              key={editing?.id || "new"}
              title={
                editing ? "Update shared dashboard" : "Create shared dashboard"
              }
              submit={
                editing ? "Save dashboard revision" : "Create shared dashboard"
              }
              onSubmit={async (form) => {
                if (missing.length || chosen.length < 1 || chosen.length > 8)
                  throw new Error(
                    "DASHBOARD_CHOOSE_ONE_TO_EIGHT_CURRENT_VIEWS",
                  );
                const saved: Detail = await json(api, base + "/dashboards", {
                  dashboard_id: editing?.id || newId.current,
                  key: editing?.key || form.key,
                  expected_revision: editing?.revision || 0,
                  environment,
                  panels: chosen.map((id) => ({
                    view_id: id,
                    revision: available.find((row) => row.id === id)!.revision,
                  })),
                });
                newId.current = crypto.randomUUID();
                setEditing(null);
                setChosen([]);
                setListing(await api(base + "/dashboards"));
                await open(saved.id);
                return "Shared dashboard saved with the reviewed view revisions.";
              }}
            >
              <Field
                label="Dashboard key"
                name="key"
                required
                defaultValue={editing?.key || ""}
                readOnly={Boolean(editing)}
              />
              <Select
                label="Dashboard environment"
                name="environment"
                value={environment}
                disabled={Boolean(editing)}
                onChange={(event) => {
                  setEnvironment(event.target.value);
                  setChosen([]);
                }}
              >
                {environments.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </Select>
              <fieldset>
                <legend>Views to include (one to eight)</legend>
                {available.map((row) => (
                  <label key={row.id}>
                    <input
                      type="checkbox"
                      checked={chosen.includes(row.id)}
                      onChange={(event) =>
                        setChosen((prior) =>
                          event.target.checked
                            ? [...prior, row.id]
                            : prior.filter((id) => id !== row.id),
                        )
                      }
                    />
                    Include {row.key} (revision {row.revision})
                  </label>
                ))}
                {missing.map((id) => (
                  <p key={id}>
                    Source view is unavailable.{" "}
                    <button
                      type="button"
                      onClick={() =>
                        setChosen((prior) =>
                          prior.filter((value) => value !== id),
                        )
                      }
                    >
                      Remove unavailable view {id.slice(0, 8)}
                    </button>
                  </p>
                ))}
              </fieldset>
              <Check
                label="I reviewed the selected saved-view revisions and filters"
                name="reviewed"
                required
              />
            </Form>
            {editing && (
              <button
                type="button"
                onClick={() => {
                  setEditing(null);
                  setChosen([]);
                }}
              >
                Cancel dashboard edit
              </button>
            )}
          </Editor>
          <Editor title="Save a trace view for your team">
            <Form
              title="Save team trace view"
              submit="Save team trace view"
              onSubmit={async (form) => {
                await json(api, base + "/saved-views", {
                  view_id: traceViewId.current,
                  key: form.key,
                  expected_revision: 0,
                  environment: form.environment,
                  page: "traces",
                  filter: {
                    workflow: form.workflow || null,
                    service: form.service || null,
                    release: form.release || null,
                  },
                });
                traceViewId.current = crypto.randomUUID();
                await reload();
                return "Trace view saved for your team.";
              }}
            >
              <Field label="Trace view key" name="key" required />
              <Field
                label="Trace view environment"
                name="environment"
                defaultValue="staging"
                required
              />
              <Field label="Trace view workflow" name="workflow" />
              <Field label="Trace view service" name="service" />
              <Field label="Trace view release" name="release" />
            </Form>
          </Editor>
        </>
      )}
    </section>
  );
}
