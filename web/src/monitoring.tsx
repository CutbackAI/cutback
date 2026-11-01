import React, { useEffect, useRef, useState } from "react";
import {
  Editor,
  Field,
  Form,
  Select,
  Check,
  explain,
  json,
  type API,
  type Data,
} from "./ui";
import { Investigation } from "./traces";
import { Quality } from "./quality";
import { SharedDashboards } from "./shared-dashboards";

const label = (value: unknown) =>
  String(value || "not observed").replaceAll("_", " ");

export function Monitoring({
  api,
  base,
  data,
  admin,
  editor,
  reload,
  onReview,
}: {
  api: API;
  base: string;
  data: Data;
  admin: boolean;
  editor: boolean;
  reload: () => Promise<void>;
  onReview: (id: string) => void;
}) {
  const [selected, setSelected] = useState<Data | null>(null);
  const [checks, setChecks] = useState<Data | null>(null);
  const [trace, setTrace] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<Data | null>(null);
  const [kind, setKind] = useState("execution_ok");
  const [environment, setEnvironment] = useState("staging");
  const [workflow, setWorkflow] = useState("");
  const [state, setState] = useState("all");
  const [moreRules, setMoreRules] = useState<Data[]>([]);
  const [moreIncidents, setMoreIncidents] = useState<Data[]>([]);
  const [moreAlerts, setMoreAlerts] = useState<Data[]>([]);
  const [moreDeliveries, setMoreDeliveries] = useState<Data[]>([]);
  const [alertCursor, setAlertCursor] = useState<string | undefined>();
  const [deliveryCursor, setDeliveryCursor] = useState<string | undefined>();
  const [windowHistory, setWindowHistory] = useState<Data | null>(null);
  const [rulesCursor, setRulesCursor] = useState<string | undefined>();
  const [incidentsCursor, setIncidentsCursor] = useState<string | undefined>();
  const generation = useRef(0);
  const rules: Data[] = [...(data.online.items || []), ...moreRules];
  const incidents: Data[] = [...(data.incidents.items || []), ...moreIncidents];
  const alerts: Data[] = [...data.alerts.items, ...moreAlerts];
  const deliveries: Data[] = [...data.deliveries.items, ...moreDeliveries];
  useEffect(() => {
    setMoreAlerts([]);
    setMoreDeliveries([]);
    setMoreRules([]);
    setMoreIncidents([]);
    setAlertCursor(undefined);
    setDeliveryCursor(undefined);
    setRulesCursor(undefined);
    setIncidentsCursor(undefined);
  }, [data]);
  const currentRules = rules.filter((r) => r.enabled);
  const shownRules = rules.filter(
    (r) =>
      r.specification.environment === environment &&
      (!workflow || r.specification.filter.workflow === workflow),
  );
  const shownIncidents = incidents.filter(
    (r) =>
      (state === "all" || state === r.state) &&
      shownRules.some((rule) =>
        alerts.some(
          (a: Data) => a.id === r.alert_rule_id && a.online_rule_id === rule.id,
        ),
      ),
  );
  async function action(fn: () => Promise<void>) {
    setError("");
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      setError(explain(e));
    } finally {
      setBusy(false);
    }
  }
  async function refresh() {
    generation.current++;
    setSelected(null);
    setChecks(null);
    setTrace("");
    setMoreRules([]);
    setWindowHistory(null);
    setMoreIncidents([]);
    setRulesCursor(undefined);
    setIncidentsCursor(undefined);
    await reload();
  }
  async function openIncident(id: string) {
    const request = ++generation.current;
    setSelected(null);
    setChecks(null);
    setTrace("");
    const [detail, evaluations] = await Promise.all([
      api(base + "/incidents/" + id),
      api(base + "/incidents/" + id + "/evaluations"),
    ]);
    if (request === generation.current) {
      setSelected(detail);
      setChecks(evaluations);
    }
  }
  async function transition(actionName: string, owner?: string) {
    const incident = selected!.incident;
    await json(api, base + "/incidents/" + incident.id + "/transition", {
      expected_revision: incident.revision,
      action: actionName,
      ...(owner ? { owner } : {}),
    });
    await reload();
    await openIncident(incident.id);
  }
  return (
    <section aria-label="Continuous quality monitoring">
      <Quality api={api} base={base} admin={admin} />
      <p>
        Turn observed failures into an incident, investigate the retained trace,
        then send authorized captured cases to Review. Metadata execution checks
        measure technical behavior; external scores retain their submitting
        evaluator’s trust level.
      </p>
      <p className="hint">
        No data is not healthy. Population and retention coverage are unknown.
        The metadata rules below make no model calls. Notifications require a
        separately approved destination and enabled delivery worker. An operator
        enables the online worker separately.
      </p>
      <button disabled={busy} onClick={() => action(refresh)}>
        Refresh monitoring
      </button>
      {error && <p role="alert">{error}</p>}
      <div className="form-grid">
        <Select
          label="Monitoring environment"
          name="monitor-environment"
          value={environment}
          onChange={(e) => {
            setEnvironment(e.target.value);
            generation.current++;
            setSelected(null);
            setTrace("");
          }}
        >
          {[
            ...new Set([
              "staging",
              ...rules.map((r) => r.specification.environment),
            ]),
          ].map((v) => (
            <option key={v}>{v}</option>
          ))}
        </Select>
        <Field
          label="Exact workflow filter"
          name="monitor-workflow"
          value={workflow}
          onChange={(e) => setWorkflow(e.target.value)}
          placeholder="All observed workflows"
        />
        <Select
          label="Incident state"
          name="incident-state"
          value={state}
          onChange={(e) => setState(e.target.value)}
        >
          {["all", "open", "acknowledged", "resolved"].map((v) => (
            <option key={v} value={v}>
              {label(v)}
            </option>
          ))}
        </Select>
      </div>
      <h2>Evaluation rules</h2>
      <p>
        {data.online.pending_intake ?? "Unknown"} retained spans await rule
        selection. Sampling and evaluation occur in the separately enabled
        worker.
      </p>
      {!shownRules.length && (
        <p>
          No rules in this loaded selection. Create a versioned rule to begin.
        </p>
      )}
      {shownRules.map((r) => (
        <article className="record" key={r.id}>
          <h3>
            {r.key} · version {r.revision}
          </h3>
          <p>
            {label(r.specification.evaluator.kind)} ·{" "}
            {r.enabled ? "enabled" : "disabled"} · {r.evaluations_used} /{" "}
            {r.specification.max_evaluations} evaluations reserved ·{" "}
            {r.specification.sample_basis_points / 100}% deterministic sample
          </p>
          <p>
            Backfill {r.backfill_complete ? "sweep complete" : "pending"}.
            Observed interval ends{" "}
            {new Date(r.specification.expires_at).toLocaleString()}. Total
            provider budget: $0.
          </p>
          {admin && (
            <>
              <button
                disabled={busy}
                onClick={() => {
                  setDraft(r);
                  setKind(r.specification.evaluator.kind);
                }}
              >
                Prepare next rule version: {r.key}
              </button>
              {r.enabled && (
                <button
                  disabled={busy}
                  onClick={() =>
                    action(async () => {
                      await json(
                        api,
                        base + "/online-rules/" + r.id + "/switch",
                        {
                          expected_revision: r.revision,
                          expected_enabled: r.enabled,
                          enabled: false,
                        },
                      );
                      await refresh();
                    })
                  }
                >
                  Disable rule: {r.key}
                </button>
              )}
            </>
          )}
        </article>
      ))}
      {(rulesCursor ?? data.online.next_cursor) && (
        <button
          disabled={busy}
          onClick={() =>
            action(async () => {
              const next = await api(
                base +
                  "/online-rules?after=" +
                  encodeURIComponent(rulesCursor ?? data.online.next_cursor),
              );
              setMoreRules((v) => [...v, ...next.items]);
              setRulesCursor(next.next_cursor || "");
            })
          }
        >
          Load more rules
        </button>
      )}
      {admin && (
        <Editor
          title={draft ? "Prepare a new version" : "Create an online rule"}
          open={!!draft}
        >
          <Form
            key={draft?.id || "new"}
            title="Versioned evaluation rule"
            submit="Approve metadata rule"
            onSubmit={async (f) => {
              const evaluator: Data = { kind };
              if (kind === "duration_le")
                evaluator.max_duration_ms = String(f.duration);
              if (kind === "external_boolean")
                Object.assign(evaluator, {
                  source: f.source,
                  evaluator: f.evaluator,
                  evaluator_version: f.evaluator_version,
                });
              const body = {
                version_id: crypto.randomUUID(),
                key: f.key,
                expected_revision: draft?.revision || 0,
                environment: f.environment,
                filter: { workflow: f.workflow || null },
                evaluator,
                sample_basis_points: Number(f.sample),
                starts_at: new Date(
                  Date.now() - Number(f.backfill) * 3600000,
                ).toISOString(),
                expires_at: new Date(
                  Date.now() + Number(f.hours) * 3600000,
                ).toISOString(),
                max_evaluations: Number(f.max_evaluations),
                max_total_usd: "0",
                wait_seconds: Number(f.wait),
                approval_ref: f.approval_ref,
              };
              await json(api, base + "/online-rules", body);
              setDraft(null);
              await refresh();
              return "Rule approved. The enabled worker will evaluate metadata outside the application request.";
            }}
          >
            <Field
              label="Rule key"
              name="key"
              required
              defaultValue={draft?.key || "execution-health"}
              readOnly={!!draft}
            />
            <Field
              label="Environment"
              name="environment"
              required
              defaultValue={draft?.specification.environment || environment}
            />
            <Field
              label="Workflow (blank matches all)"
              name="workflow"
              defaultValue={draft?.specification.filter.workflow || workflow}
            />
            <Select
              label="Evaluator"
              name="kind"
              value={kind}
              onChange={(e) => setKind(e.target.value)}
            >
              <option value="execution_ok">Execution status</option>
              <option value="duration_le">Maximum observed duration</option>
              <option value="external_boolean">
                Exact external boolean evaluator
              </option>
            </Select>
            {kind === "duration_le" && (
              <Field
                label="Maximum duration (ms)"
                name="duration"
                type="number"
                min="0"
                max="86400000"
                step="any"
                required
                defaultValue={
                  draft?.specification.evaluator.max_duration_ms || "1000"
                }
              />
            )}
            {kind === "external_boolean" && (
              <>
                <Field
                  label="External source"
                  name="source"
                  required
                  defaultValue={draft?.specification.evaluator.source}
                />
                <Field
                  label="External evaluator"
                  name="evaluator"
                  required
                  defaultValue={draft?.specification.evaluator.evaluator}
                />
                <Field
                  label="Exact evaluator version"
                  name="evaluator_version"
                  required
                  defaultValue={
                    draft?.specification.evaluator.evaluator_version
                  }
                />
              </>
            )}
            <Field
              label="Sample basis points (10000 = all)"
              name="sample"
              type="number"
              min="1"
              max="10000"
              required
              defaultValue={draft?.specification.sample_basis_points || "10000"}
            />
            <Field
              label="Backfill hours"
              name="backfill"
              type="number"
              min="0"
              max="360"
              required
              defaultValue="1"
            />
            <Field
              label="Active hours"
              name="hours"
              type="number"
              min="1"
              max="360"
              required
              defaultValue="24"
            />
            <Field
              label="Maximum evaluations"
              name="max_evaluations"
              type="number"
              min="1"
              max="50000"
              required
              defaultValue={draft?.specification.max_evaluations || "1000"}
            />
            <Field
              label="Wait for external scores (seconds)"
              name="wait"
              type="number"
              min="1"
              max="86400"
              required
              defaultValue={draft?.specification.wait_seconds || "300"}
            />
            <Field label="Approval reference" name="approval_ref" required />
          </Form>
          {draft && (
            <button onClick={() => setDraft(null)}>Cancel new version</button>
          )}
        </Editor>
      )}
      <h2>Alert windows</h2>
      {!data.alerts.items.length && (
        <p>No alert rules. Create one after defining an evaluation rule.</p>
      )}
      {alerts.map((r: Data) => (
        <article key={r.id}>
          <h3>
            {r.key} · version {r.revision}
            {r.enabled ? "" : " · disabled"}
          </h3>
          <p>
            {label(r.latest_window.status || "not evaluated")} ·{" "}
            {label(r.specification.signal)} · minimum{" "}
            {r.specification.min_samples} observations
          </p>
          {r.latest_window.counts && (
            <p>
              Passed {r.latest_window.counts.passed}; failed{" "}
              {r.latest_window.counts.failed}; evaluator errors{" "}
              {r.latest_window.counts.error}; missing{" "}
              {r.latest_window.counts.missing}; unknown{" "}
              {r.latest_window.counts.unknown}; pending{" "}
              {r.latest_window.counts.queued + r.latest_window.counts.leased}.
            </p>
          )}
        </article>
      ))}
      {(alertCursor ?? data.alerts.next_cursor) && (
        <button
          disabled={busy}
          onClick={() =>
            action(async () => {
              const next = await api(
                base +
                  "/alert-rules?after=" +
                  encodeURIComponent(alertCursor ?? data.alerts.next_cursor),
              );
              setMoreAlerts((v) => [...v, ...next.items]);
              setAlertCursor(next.next_cursor || "");
            })
          }
        >
          Load more alert rules
        </button>
      )}
      {alerts.map((r: Data) => (
        <div key={r.id}>
          {r.window_queue_full && (
            <p role="status">
              Historical window receipt limit reached. Coverage is unknown;
              review retention and audit events.
            </p>
          )}
          <button
            disabled={busy}
            onClick={() =>
              action(async () => {
                const request = generation.current;
                const value = await api(
                  base + "/alert-rules/" + r.id + "/windows",
                );
                if (request === generation.current)
                  setWindowHistory({ ...value, rule_id: r.id, key: r.key });
              })
            }
          >
            Inspect window history: {r.key}
          </button>
          {admin && r.enabled && (
            <button
              disabled={busy}
              onClick={() =>
                action(async () => {
                  await json(api, base + "/alert-rules/" + r.id + "/switch", {
                    expected_revision: r.revision,
                    expected_enabled: true,
                    enabled: false,
                  });
                  await refresh();
                })
              }
            >
              Disable alert: {r.key}
            </button>
          )}
        </div>
      ))}
      {windowHistory && (
        <section aria-label="Retained alert window history">
          <h3>Retained windows: {windowHistory.key}</h3>
          {windowHistory.items.map((w: Data) => (
            <p key={w.id}>
              {new Date(w.window_start).toLocaleString()} ·{" "}
              {label(w.summary.status)} ·{" "}
              {w.dirty ? "awaiting re-evaluation" : "evaluated"} · failed{" "}
              {w.summary.counts?.failed ?? "unknown"}; evaluator errors{" "}
              {w.summary.counts?.error ?? "unknown"}; missing{" "}
              {w.summary.counts?.missing ?? "unknown"}
            </p>
          ))}
          {!windowHistory.items.length && <p>No retained windows.</p>}
          {windowHistory.next_cursor && (
            <button
              disabled={busy}
              onClick={() =>
                action(async () => {
                  const request = generation.current;
                  const next = await api(
                    base +
                      "/alert-rules/" +
                      windowHistory.rule_id +
                      "/windows?after=" +
                      windowHistory.next_cursor,
                  );
                  if (request === generation.current)
                    setWindowHistory({
                      ...windowHistory,
                      ...next,
                      items: [...windowHistory.items, ...next.items],
                    });
                })
              }
            >
              Load more windows
            </button>
          )}
        </section>
      )}
      {admin && (
        <Editor title="Create an alert rule">
          <Form
            title="Versioned alert rule"
            submit="Create alert rule"
            onSubmit={async (f) => {
              await json(api, base + "/alert-rules", {
                version_id: crypto.randomUUID(),
                key: f.key,
                expected_revision: 0,
                online_rule_id: f.rule,
                window_seconds: Number(f.window),
                grace_seconds: Number(f.grace),
                min_samples: Number(f.minimum),
                signal: f.signal,
                threshold: String(f.threshold),
                cooldown_seconds: Number(f.cooldown),
              });
              await refresh();
              return "Alert rule created. Incidents appear after a closed window meets the minimum sample requirement.";
            }}
          >
            <Field label="Alert key" name="key" required />
            <Select label="Evaluation rule version" name="rule" required>
              <option value="">Choose a rule</option>
              {currentRules.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.key} · version {r.revision}
                </option>
              ))}
            </Select>
            <Select label="Signal" name="signal">
              <option value="failure_rate">Observed failure rate</option>
              <option value="evaluator_error_rate">Evaluator error rate</option>
              <option value="missing_rate">Missing result rate</option>
            </Select>
            <Field
              label="Window (seconds)"
              name="window"
              type="number"
              required
              min="60"
              max="86400"
              defaultValue="300"
            />
            <Field
              label="Late-score grace (seconds)"
              name="grace"
              type="number"
              required
              min="0"
              max="86400"
              defaultValue="300"
            />
            <Field
              label="Minimum samples"
              name="minimum"
              type="number"
              required
              min="1"
              max="50000"
              defaultValue="10"
            />
            <Field
              label="Threshold (0 to 1)"
              name="threshold"
              type="number"
              required
              min="0.000001"
              max="1"
              step="any"
              defaultValue="0.1"
            />
            <Field
              label="Cooldown (seconds)"
              name="cooldown"
              type="number"
              required
              min="0"
              max="2592000"
              defaultValue="3600"
            />
          </Form>
        </Editor>
      )}
      <h2>Incidents</h2>
      {!shownIncidents.length && (
        <p>
          No incidents in this loaded selection. This does not establish healthy
          traffic.
        </p>
      )}
      {shownIncidents.map((r) => (
        <article key={r.id}>
          <h3>
            {label(r.evidence.signal)} · {label(r.state)}
          </h3>
          <p>
            {new Date(r.window_start).toLocaleString()} · owner{" "}
            {r.owner || "unassigned"}
          </p>
          <button
            disabled={busy}
            onClick={() => action(() => openIncident(r.id))}
          >
            Open incident {r.id.slice(0, 8)}
          </button>
        </article>
      ))}
      {(incidentsCursor ?? data.incidents.next_cursor) && (
        <button
          disabled={busy}
          onClick={() =>
            action(async () => {
              const next = await api(
                base +
                  "/incidents?after=" +
                  encodeURIComponent(
                    incidentsCursor ?? data.incidents.next_cursor,
                  ),
              );
              setMoreIncidents((v) => [...v, ...next.items]);
              setIncidentsCursor(next.next_cursor || "");
            })
          }
        >
          Load more incidents
        </button>
      )}
      {selected && (
        <section aria-label="Incident investigation">
          <h2>Incident investigation</h2>
          <p>
            State: {label(selected.incident.state)} · revision{" "}
            {selected.incident.revision} · owner{" "}
            {selected.incident.owner || "unassigned"}
          </p>
          <p>
            Evidence is the retained sampled window. Missing content and
            unobserved traffic cannot be reconstructed.
          </p>
          {editor && selected.incident.state !== "resolved" && (
            <>
              {selected.incident.state === "open" && (
                <button
                  disabled={busy}
                  onClick={() => action(() => transition("acknowledge"))}
                >
                  Acknowledge incident
                </button>
              )}
              <button
                disabled={busy}
                onClick={() => action(() => transition("resolve"))}
              >
                Resolve incident
              </button>
              <Form
                title="Incident owner"
                submit="Assign owner"
                onSubmit={async (f) => {
                  await transition("assign", String(f.owner));
                  return "Owner reference updated. This does not grant access.";
                }}
              >
                <Field
                  label="Team owner reference"
                  name="owner"
                  required
                  defaultValue={selected.incident.owner || ""}
                />
              </Form>
            </>
          )}
          <ol>
            {selected.events.map((e: Data) => (
              <li key={e.id}>
                {label(e.action)} · {e.actor} · revision {e.revision}
              </li>
            ))}
          </ol>
          {selected.events_truncated && (
            <p>
              Event history truncated after 500 entries. The metadata export
              retains the complete available history.
            </p>
          )}
          <h3>Retained evaluation receipts</h3>
          {!checks?.items.length && (
            <p>No evaluation receipts remain in retention.</p>
          )}
          {checks?.items.map((r: Data) => (
            <article key={r.id}>
              <p>
                {label(r.state)} · {label(r.result.code)} · span {r.span_id}
              </p>
              {r.trace_record_id ? (
                <button
                  disabled={busy}
                  onClick={() => setTrace(r.trace_record_id)}
                >
                  Investigate retained trace {r.span_id}
                </button>
              ) : (
                <p>Source trace expired.</p>
              )}
            </article>
          ))}
          {checks?.next_cursor && (
            <button
              disabled={busy}
              onClick={() =>
                action(async () => {
                  const id = selected.incident.id;
                  const request = generation.current;
                  const next = await api(
                    base +
                      "/incidents/" +
                      id +
                      "/evaluations?after=" +
                      checks.next_cursor,
                  );
                  if (request === generation.current)
                    setChecks({
                      ...next,
                      items: [...checks.items, ...next.items],
                    });
                })
              }
            >
              Load more evidence
            </button>
          )}
          {trace && (
            <Investigation
              key={trace}
              api={api}
              base={base}
              id={trace}
              allowContent={admin}
              onReview={onReview}
            />
          )}
        </section>
      )}
      <h2>Incident notifications</h2>
      <p>
        Delivery is off until an administrator approves an operator-allowlisted
        destination and enables its worker. Payloads contain incident
        identifiers and state only. Receivers must deduplicate delivery IDs;
        retries can repeat a notification.
      </p>
      {!data.notifications.items.length && (
        <p>No notification destinations approved.</p>
      )}
      {data.notifications.items.map((r: Data) => (
        <p key={r.id}>
          {r.destination} · {r.enabled ? "approved" : "disabled"} · revision{" "}
          {r.revision}
          {admin && r.enabled && (
            <button
              disabled={busy}
              onClick={() =>
                action(async () => {
                  await json(api, base + "/notifications", {
                    destination: r.destination,
                    expected_revision: r.revision,
                    enabled: false,
                    authorize_delivery: false,
                    approval_ref: "dashboard-disable",
                  });
                  await refresh();
                })
              }
            >
              Disable notifications: {r.destination}
            </button>
          )}
        </p>
      ))}
      {admin && !!data.destinations.identifiers.length && (
        <Editor title="Approve incident notifications">
          <Form
            title="External delivery approval"
            submit="Approve future metadata deliveries"
            onSubmit={async (f) => {
              const config = data.notifications.items.find(
                (r: Data) => r.destination === f.destination,
              );
              await json(api, base + "/notifications", {
                destination: f.destination,
                expected_revision: config?.revision || 0,
                enabled: true,
                authorize_delivery: f.authorization === "on",
                approval_ref: f.approval_ref,
              });
              await refresh();
              return "Future incident metadata delivery approved for this configured destination.";
            }}
          >
            <Select
              label="Operator-allowlisted destination"
              name="destination"
              required
            >
              <option value="">Choose a destination</option>
              {data.destinations.identifiers.map((v: string) => (
                <option key={v}>{v}</option>
              ))}
            </Select>
            <Field
              label="Delivery approval reference"
              name="approval_ref"
              required
            />
            <Check
              label="I authorize future external delivery of incident metadata to this destination"
              name="authorization"
              required
            />
          </Form>
        </Editor>
      )}
      {deliveries.map((r: Data) => (
        <p key={r.id}>
          Delivery {r.id.slice(0, 8)} · {label(r.state)} · attempts {r.attempts}{" "}
          · {label(r.code)}
        </p>
      ))}
      {(deliveryCursor ?? data.deliveries.next_cursor) && (
        <button
          disabled={busy}
          onClick={() =>
            action(async () => {
              const next = await api(
                base +
                  "/notification-deliveries?after=" +
                  encodeURIComponent(
                    deliveryCursor ?? data.deliveries.next_cursor,
                  ),
              );
              setMoreDeliveries((v) => [...v, ...next.items]);
              setDeliveryCursor(next.next_cursor || "");
            })
          }
        >
          Load more delivery receipts
        </button>
      )}
      <SharedDashboards
        api={api}
        base={base}
        editor={editor}
        views={data.savedViews.items}
        reload={reload}
        onIncident={openIncident}
      />
      <h2>Shared saved views</h2>
      <p>
        Views contain typed filters, never credentials or raw query code.
        Filters apply to the loaded pages; use pagination to inspect more
        records.
      </p>
      {data.savedViews.items.map((r: Data) => (
        <p key={r.id}>
          <button
            onClick={() => {
              generation.current++;
              setSelected(null);
              setTrace("");
              setEnvironment(r.specification.environment);
              setWorkflow(r.specification.filter.workflow || "");
              setState(r.specification.incident_state || "all");
            }}
          >
            {r.key}
          </button>
          {editor && (
            <button
              disabled={busy}
              onClick={() =>
                action(async () => {
                  await api(
                    base +
                      "/saved-views/" +
                      r.id +
                      "?expected_revision=" +
                      r.revision,
                    { method: "DELETE" },
                  );
                  await refresh();
                })
              }
            >
              Delete view {r.key}
            </button>
          )}
        </p>
      ))}
      {editor && (
        <Form
          title="Save incident selection"
          submit="Save shared view"
          onSubmit={async (f) => {
            await json(api, base + "/saved-views", {
              view_id: crypto.randomUUID(),
              key: f.key,
              expected_revision: 0,
              environment,
              filter: { workflow: workflow || null },
              page: "incidents",
              incident_state: state === "all" ? null : state,
            });
            await reload();
            return "Shared metadata view saved.";
          }}
        >
          <Field label="Saved view key" name="key" required />
        </Form>
      )}
    </section>
  );
}
