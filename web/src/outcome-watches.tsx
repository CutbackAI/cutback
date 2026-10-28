import React, { useEffect, useRef, useState } from "react";
import type { components } from "./api.generated";
import {
  type API,
  type Data,
  Check,
  Editor,
  Field,
  Form,
  Select,
  explain,
  json,
  money,
} from "./ui";

type Watch = components["schemas"]["WatchReceipt"];
type Snapshot = components["schemas"]["TaskSnapshotBrief"];
type WindowPage = components["schemas"]["WatchWindowPage"];

const actions: Record<string, string> = {
  REVIEW_ROLLBACK: "Review a possible outcome regression",
  REVIEW_COST_REGRESSION: "Review a possible cost regression",
  COLLECT_MISSING_EVIDENCE: "Collect missing evidence",
  PREPARE_INDEPENDENT_CONFIRMATION: "Prepare independent confirmation",
};

function localTime(date: Date) {
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}

export function OutcomeWatches({
  api,
  base,
  environment,
  canWrite,
  canApprove,
  newSnapshot,
}: {
  api: API;
  base: string;
  environment: string;
  canWrite: boolean;
  canApprove: boolean;
  newSnapshot?: string;
}) {
  const [rows, setRows] = useState<Watch[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [snapshotCursor, setSnapshotCursor] = useState<string | null>(null);
  const [changes, setChanges] = useState<Data[]>([]);
  const [changeOffset, setChangeOffset] = useState(0);
  const [moreChanges, setMoreChanges] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<Watch | null>(null);
  const [windows, setWindows] = useState<WindowPage | null>(null);
  const [windowBusy, setWindowBusy] = useState(false);
  const version = useRef(0);
  const baselineVersion = useRef(0);
  const sourceVersion = useRef(0);
  const [baseline, setBaseline] = useState(newSnapshot || "");
  const windowVersion = useRef(0);
  const request = useRef<{ intent: string; id: string } | null>(null);
  const [firstEnd] = useState(() => localTime(new Date(Date.now() + 3600000)));
  const [expiry] = useState(() =>
    localTime(new Date(Date.now() + 7 * 86400000)),
  );

  async function load(after?: string) {
    const current = ++version.current;
    setBusy(true);
    setError("");
    try {
      const page = await api(
        `${base}/outcome-watches?environment_id=${environment}&limit=20${after ? `&after=${after}` : ""}`,
      );
      if (current === version.current) {
        setRows((previous) =>
          after ? [...previous, ...page.items] : page.items,
        );
        setCursor(page.next_after);
      }
    } catch (e) {
      if (current === version.current) setError(explain(e));
    } finally {
      if (current === version.current) setBusy(false);
    }
  }
  async function loadSnapshots(after?: string) {
    const current = ++baselineVersion.current;
    try {
      const page = await api(
        `${base}/task-cost-snapshots?environment_id=${environment}&limit=20${after ? `&after=${after}` : ""}`,
      );
      const recent: Snapshot | null = !after && newSnapshot &&
        !page.items.some((row: Snapshot) => row.id === newSnapshot)
        ? await api(`${base}/task-cost-snapshots/${newSnapshot}/summary`)
        : null;
      if (current === baselineVersion.current) {
        setSnapshots((previous) => Array.from(new Map<string, Snapshot>(
          (after ? [...previous, ...page.items] : [...(recent ? [recent] : []), ...page.items])
            .map((row: Snapshot) => [row.id, row]),
        ).values()));
        setSnapshotCursor(page.next_after);
      }
    } catch (e) {
      if (current === baselineVersion.current) setError(explain(e));
    }
  }
  async function loadChanges(offset = 0) {
    const current = ++sourceVersion.current;
    try {
      const sources = await api(`${base}/changes?offset=${offset}`);
      if (current === sourceVersion.current) {
        setChanges((previous) =>
          offset ? [...previous, ...sources] : sources,
        );
        setChangeOffset(offset);
        setMoreChanges(sources.length === 100);
      }
    } catch (e) {
      if (current === sourceVersion.current) setError(explain(e));
    }
  }
  useEffect(() => {
    setRows([]);
    setSnapshots([]);
    setChanges([]);
    setSelected(null);
    setWindows(null);
    request.current = null;
    void load();
    void loadSnapshots();
    void loadChanges();
    setBaseline(newSnapshot || "");
    setSnapshotCursor(null);
    setMoreChanges(false);
    setChangeOffset(0);
    return () => {
      version.current++;
      windowVersion.current++;
      baselineVersion.current++;
      sourceVersion.current++;
    };
  }, [api, base, environment, newSnapshot]);

  async function open(watch: Watch, after?: number) {
    const current = ++windowVersion.current;
    setSelected(watch);
    setWindowBusy(true);
    setError("");
    if (after == null) setWindows(null);
    try {
      const page: WindowPage = await api(
        `${base}/outcome-watches/${watch.id}/windows?limit=20${after == null ? "" : `&after=${after}`}`,
      );
      if (current === windowVersion.current)
        setWindows((previous) =>
          after == null || !previous
            ? page
            : { ...page, items: [...previous.items, ...page.items] },
        );
    } catch (e) {
      if (current === windowVersion.current) setError(explain(e));
    } finally {
      if (current === windowVersion.current) setWindowBusy(false);
    }
  }
  async function changeState(row: Watch, enabled: boolean) {
    setBusy(true);
    setError("");
    try {
      await json(api, `${base}/outcome-watches/${row.id}/state`, {
        approval_hash: row.approval_hash,
        enabled,
      });
      await load();
    } catch (e) {
      setError(explain(e));
      setBusy(false);
    }
  }
  return (
    <section aria-label="Automatic change observations">
      <h2>Automatic change observations</h2>
      <p>
        Check later declared task outcomes against a frozen baseline. A finite
        approved policy records review actions automatically; it makes no model
        calls and changes no traffic.
      </p>
      <button disabled={busy} onClick={() => load()}>
        Refresh observation policies
      </button>
      {error && <p role="alert">{error}</p>}
      {!busy && !rows.length && (
        <p>No observation policies in this environment.</p>
      )}
      {rows.map((row) => {
        const expired = Date.now() >= Date.parse(row.specification.policy.expires_at);
        const terminal = expired || ["EXPIRED", "EXHAUSTED"].includes(row.status);
        return (
        <article className="editor" key={row.id}>
          <h3>
            {row.specification.policy.key} · {row.id.slice(0, 8)}
          </h3>
          <p>
            {(expired ? "EXPIRED" : row.status).replaceAll("_", " ")} · release{" "}
            {row.specification.policy.candidate_release}.{" "}
            {row.windows_processed} of {row.specification.policy.max_windows}{" "}
            checks used; {row.retained_windows} receipts retained;{" "}
            {row.windows_skipped} intervals skipped.
          </p>
          <p>
            Expires{" "}
            {new Date(row.specification.policy.expires_at).toLocaleString()}.{" "}
            {row.last_code?.replaceAll("_", " ")}
          </p>
          <details>
            <summary>Review the frozen policy</summary>
            <p>
              Approval SHA-256: <code>{row.approval_hash}</code>
            </p>
            <pre tabIndex={0}>{JSON.stringify(row.specification, null, 2)}</pre>
          </details>
          <button disabled={windowBusy} onClick={() => open(row)}>
            Inspect observation history {row.id.slice(0, 8)}
          </button>
          {canApprove && !terminal && (
            <>
              {row.enabled ? (
                <button disabled={busy} onClick={() => changeState(row, false)}>
                  Pause policy {row.id.slice(0, 8)}
                </button>
              ) : row.approved_at && row.status === "PAUSED" ? (
                <button disabled={busy} onClick={() => changeState(row, true)}>
                  Resume policy {row.id.slice(0, 8)}
                </button>
              ) : null}
              {(!row.approved_at || row.status === "BLOCKED") &&
                !["EXPIRED", "EXHAUSTED"].includes(row.status) && (
                  <Form
                    title={`Approve observation policy ${row.id.slice(0, 8)}`}
                    submit="Approve this observation policy"
                    onSubmit={async (data) => {
                      await json(
                        api,
                        `${base}/outcome-watches/${row.id}/approve`,
                        {
                          approval_hash: row.approval_hash,
                          approval_ref: String(data.approval_ref),
                        },
                      );
                      await load();
                      return "Exact policy approved. An operator must enable metadata observation on the worker.";
                    }}
                  >
                    <Field
                      label={`Policy approval reference ${row.id.slice(0, 8)}`}
                      name="approval_ref"
                      required
                      maxLength={200}
                      pattern="[A-Za-z0-9_.:/-]+"
                    />
                  </Form>
                )}
            </>
          )}
        </article>
      );})}
      {cursor && (
        <button disabled={busy} onClick={() => load(cursor)}>
          Load more observation policies
        </button>
      )}
      {selected && (
        <section aria-label="Automatic observation history">
          <h3>Observed windows for {selected.specification.policy.key}</h3>
          <button disabled={windowBusy} onClick={() => open(selected)}>
            Refresh observation history
          </button>
          {windowBusy && <p role="status">Reading retained windows…</p>}
          {windows && (
            <>
              <p>
                {windows.retained_windows} receipts retained for{" "}
                {windows.windows_processed} processed checks. Missing receipts
                cannot reconstruct prior windows. These observations use
                producer-declared outcomes and owner-attested release linkage.
              </p>
              {!windows.items.length ? (
                <p>
                  No retained windows yet. A pending policy, disabled worker,
                  future interval or retention can leave this list empty.
                </p>
              ) : (
                <div className="table-scroll" tabIndex={0}>
                  <table>
                    <caption>Automatic observation windows</caption>
                    <thead>
                      <tr>
                        <th>Window</th>
                        <th>Declared success rate</th>
                        <th>Known search subtotal</th>
                        <th>Next review</th>
                        <th>Evidence</th>
                      </tr>
                    </thead>
                    <tbody>
                      {windows.items.map((item) => (
                        <tr key={item.id}>
                          <th scope="row">
                            {new Date(item.evidence.start).toLocaleString()} –{" "}
                            {new Date(item.evidence.end).toLocaleString()}
                          </th>
                          <td>
                            {item.evidence.report
                              ?.candidate_declared_success_rate == null
                              ? "Unknown"
                              : `${(100 * Number(item.evidence.report.candidate_declared_success_rate)).toFixed(1)}%`}
                          </td>
                          <td>
                            {money(
                              item.evidence.report
                                ?.known_optimization_run_subtotal_usd,
                            )}
                          </td>
                          <td>
                            {actions[item.evidence.next_action] ||
                              "Review incomplete evidence"}
                          </td>
                          <td>
                            <details>
                              <summary>Frozen window details</summary>
                              <p>
                                {item.evidence.reasons
                                  .map((reason) => reason.replaceAll("_", " "))
                                  .join(" · ") ||
                                  "Prepare an independent confirmation before a quality claim."}
                              </p>
                              <pre tabIndex={0}>
                                {JSON.stringify(item, null, 2)}
                              </pre>
                            </details>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {windows.next_after != null && (
                <button
                  disabled={windowBusy}
                  onClick={() => open(selected, windows.next_after!)}
                >
                  Load more observation windows
                </button>
              )}
            </>
          )}
        </section>
      )}
      {canWrite && (
        <Editor title="Prepare an automatic observation policy">
          <Form
            title="Prepare automatic change observations"
            submit="Freeze observation policy"
            description="Select one baseline and an uploaded source change. Cutback derives exact source hashes and linked optimization runs. Review the frozen policy before approval. A late declaration arriving after a frozen cutoff is not retroactively added."
            onSubmit={async (data) => {
              const body = {
                key: String(data.key),
                baseline_snapshot_id: String(data.baseline),
                change_id: String(data.change),
                release_linkage_attested: !!data.link,
                candidate_release: String(data.release),
                window_seconds: Number(data.window) * 60,
                interval_seconds: Number(data.interval) * 60,
                first_window_end: new Date(String(data.first)).toISOString(),
                grace_seconds: Number(data.grace) * 60,
                expires_at: new Date(String(data.expiry)).toISOString(),
                max_windows: Number(data.maximum),
                expected_tasks:
                  data.expected === "" ? null : Number(data.expected),
                assumptions: {
                  min_tasks_per_window: Number(data.minimum),
                  max_absolute_success_rate_loss: String(data.loss),
                  forecast_successful_tasks:
                    data.volume === "" ? null : Number(data.volume),
                  all_optimization_runs_declared_complete: !!data.all_runs,
                  other_optimization_costs_declared_complete: !!data.all_other,
                  other_optimization_costs:
                    data.other_cost === ""
                      ? []
                      : [
                          {
                            key: "declared-other-optimization",
                            kind: "other",
                            amount_usd: String(data.other_cost),
                          },
                        ],
                },
              };
              const intent = JSON.stringify({ base, environment, body });
              if (request.current?.intent !== intent)
                request.current = { intent, id: crypto.randomUUID() };
              const row: Watch = await json(
                api,
                `${base}/outcome-watches/draft?environment_id=${environment}`,
                { ...body, watch_id: request.current.id },
              );
              await load();
              return `Policy ${row.id} frozen. Review its exact hash, scope and limits before approval.`;
            }}
          >
            <Field
              label="Observation policy name"
              name="key"
              defaultValue="release-observation"
              required
              maxLength={200}
              pattern="[A-Za-z0-9_.:/-]+"
            />
            <Select
              label="Observation baseline"
              name="baseline"
              required
              value={baseline}
              onChange={(event) => setBaseline(event.target.value)}
            >
              <option value="">Select a frozen baseline</option>
              {snapshots.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.release || "Unscoped release"} ·{" "}
                  {row.workflow || "Unscoped workflow"} · {row.id.slice(0, 8)}
                </option>
              ))}
            </Select>
            {snapshotCursor && (
              <button
                type="button"
                onClick={() => loadSnapshots(snapshotCursor)}
              >
                Load more observation baselines
              </button>
            )}
            <Select label="Observed source change" name="change" required>
              <option value="">Select a reviewed source change</option>
              {changes
                .filter((row) => row.evidence?.diff_sha256)
                .map((row) => (
                  <option key={row.id} value={row.id}>
                    Source change {row.id.slice(0, 8)}
                  </option>
                ))}
            </Select>
            {moreChanges && (
              <button
                type="button"
                onClick={() => loadChanges(changeOffset + 100)}
              >
                Load more source changes
              </button>
            )}
            <Field
              label="Candidate application release"
              name="release"
              required
              maxLength={200}
              pattern="[A-Za-z0-9_.:/-]+"
            />
            <Check
              label="I attest that this source change belongs to the declared candidate release."
              name="link"
              required
            />
            <Field
              label="Observation window minutes"
              name="window"
              type="number"
              min="1"
              max="43200"
              defaultValue="60"
              required
            />
            <Field
              label="Observation interval minutes"
              name="interval"
              type="number"
              min="1"
              max="43200"
              defaultValue="60"
              required
            />
            <Field
              label="First window end (local time)"
              name="first"
              type="datetime-local"
              defaultValue={firstEnd}
              required
            />
            <Field
              label="Late declaration grace minutes"
              name="grace"
              type="number"
              min="0"
              max="1440"
              defaultValue="5"
              required
            />
            <Field
              label="Observation expiry (local time)"
              name="expiry"
              type="datetime-local"
              defaultValue={expiry}
              required
            />
            <Field
              label="Maximum observation windows"
              name="maximum"
              type="number"
              min="1"
              max="100"
              defaultValue="30"
              required
            />
            <Field
              label="Expected tasks per window (optional)"
              name="expected"
              type="number"
              min="0"
              max="1000000"
              hint="Leave empty when population coverage is unknown."
            />
            <Field
              label="Minimum declared tasks per window"
              name="minimum"
              type="number"
              min="1"
              max="10000"
              defaultValue="30"
              required
            />
            <Field
              label="Maximum declared success-rate loss (fraction)"
              name="loss"
              type="number"
              min="0"
              max="1"
              step="any"
              defaultValue="0"
              required
            />
            <Field
              label="Forecast future successful tasks (optional)"
              name="volume"
              type="number"
              min="1"
              max="1000000000"
            />
            <Check
              label="All optimization work for this change is included in the linked runs."
              name="all_runs"
            />
            <Field
              label="Other optimization costs USD (optional)"
              name="other_cost"
              type="number"
              min="0"
              step="any"
            />
            <Check
              label="The declared other optimization costs are complete."
              name="all_other"
            />
          </Form>
        </Editor>
      )}
    </section>
  );
}
