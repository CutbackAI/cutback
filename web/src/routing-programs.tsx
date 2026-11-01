import React, { useEffect, useRef, useState } from "react";
import type { components } from "./api.generated";
import {
  type API,
  type Data,
  Field,
  Form,
  Select,
  explain,
  json,
  money,
} from "./ui";

type Brief = components["schemas"]["RoutingProgramBrief"];
type Detail = components["schemas"]["RoutingProgramDetail"];
type Snapshot = components["schemas"]["TaskSnapshotBrief"];
const active = new Set([
  "READY_FOR_APPROVAL",
  "QUEUED",
  "WAITING_STATIC",
  "WAITING_TRAINING",
  "WAITING_ROUTED",
  "WAITING_CONFIRMATION",
]);
const labels: Record<string, string> = {
  READY_FOR_APPROVAL: "Review the whole budget",
  QUEUED: "Approved; waiting for worker",
  WAITING_STATIC: "Comparing simple native alternatives",
  WAITING_TRAINING: "Training after simple alternatives failed",
  WAITING_ROUTED: "Validating the fitted router",
  WAITING_CONFIRMATION: "Confirming one choice on untouched cases",
  COMPLETED: "Evaluation complete; inspect the decision",
  FAILED: "Stopped; inspect the failure and retained costs",
  CANCELLED: "Cancelled; late bills can still update costs",
  EXPIRED: "Time or content permission expired",
  ABSTAINED: "Insufficient evidence or economic opportunity",
};

export function RoutingPrograms({
  api,
  base,
  admin,
  templates,
}: {
  api: API;
  base: string;
  admin: boolean;
  templates: Data[];
}) {
  const [rows, setRows] = useState<Brief[]>([]),
    [cursor, setCursor] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [environments, setEnvironments] = useState<
    { id: string; name: string }[]
  >([]);
  const [environment, setEnvironment] = useState("");
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]),
    [snapshotCursor, setSnapshotCursor] = useState<string | null>(null);
  const [privateContent, setPrivateContent] = useState<Data | null>(null);
  const [purpose, setPurpose] = useState(""),
    [reference, setReference] = useState("");
  const [reviewed, setReviewed] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const generation = useRef(0),
    selection = useRef(0),
    envGeneration = useRef(0);
  const prepared = useRef<{ key: string; id: string } | null>(null);
  const available = templates.filter(
    (row) =>
      row.approval_status === "pending" &&
      row.specification?.routing_execution?.source,
  );

  useEffect(() => {
    const version = ++generation.current;
    selection.current++;
    setRows([]);
    setDetail(null);
    setPrivateContent(null);
    setReviewed(false);
    setPurpose("");
    setReference("");
    setError("");
    setBusy(false);
    prepared.current = null;
    Promise.all([
      api(base + "/routing/programs?limit=20"),
      api(base + "/environments"),
    ])
      .then(([page, envs]) => {
        if (version === generation.current) {
          setRows(page.items);
          setCursor(page.next_after);
          setEnvironments(envs.items);
          setEnvironment(envs.items[0]?.id || "");
        }
      })
      .catch((e) => {
        if (version === generation.current) setError(explain(e));
      });
    return () => {
      generation.current++;
      selection.current++;
    };
  }, [api, base, admin]);
  useEffect(() => {
    const version = ++envGeneration.current;
    setSnapshots([]);
    setSnapshotCursor(null);
    if (environment)
      api(base + `/task-cost-snapshots?environment_id=${environment}&limit=20`)
        .then((page) => {
          if (version === envGeneration.current) {
            setSnapshots(page.items);
            setSnapshotCursor(page.next_after);
          }
        })
        .catch((e) => {
          if (version === envGeneration.current) setError(explain(e));
        });
    return () => {
      envGeneration.current++;
    };
  }, [api, base, environment]);

  async function select(id: string) {
    const version = ++selection.current,
      project = generation.current;
    setDetail(null);
    setPrivateContent(null);
    setReviewed(false);
    setReference("");
    setError("");
    setBusy(true);
    try {
      const next = await api(base + `/routing/programs/${id}`);
      if (version === selection.current && project === generation.current)
        setDetail(next);
    } catch (e) {
      if (version === selection.current && project === generation.current)
        setError(explain(e));
    } finally {
      if (project === generation.current) setBusy(false);
    }
  }
  async function more(kind: "programs" | "snapshots") {
    const version = generation.current,
      envVersion = envGeneration.current;
    setBusy(true);
    setError("");
    try {
      const page = await api(
        base +
          (kind === "programs"
            ? `/routing/programs?limit=20&after=${cursor}`
            : `/task-cost-snapshots?environment_id=${environment}&limit=20&after=${snapshotCursor}`),
      );
      if (
        version !== generation.current ||
        envVersion !== envGeneration.current
      )
        return;
      if (kind === "programs") {
        setRows((old) => [...old, ...page.items]);
        setCursor(page.next_after);
      } else {
        setSnapshots((old) => [...old, ...page.items]);
        setSnapshotCursor(page.next_after);
      }
    } catch (e) {
      if (version === generation.current) setError(explain(e));
    } finally {
      if (version === generation.current) setBusy(false);
    }
  }
  async function readPrivate() {
    if (!detail || !purpose) return;
    const version = selection.current,
      project = generation.current;
    setBusy(true);
    setPrivateContent(null);
    setReviewed(false);
    setError("");
    try {
      const value = await json(
        api,
        base + `/routing/programs/${detail.id}/content`,
        { purpose },
      );
      if (
        version === selection.current &&
        project === generation.current &&
        value.program_hash === detail.program_hash
      )
        setPrivateContent(value);
    } catch (e) {
      if (version === selection.current && project === generation.current)
        setError(explain(e));
    } finally {
      if (project === generation.current) setBusy(false);
    }
  }
  async function act(action: "approve" | "cancel") {
    if (!detail) return;
    const version = selection.current,
      project = generation.current,
      id = detail.id;
    setBusy(true);
    setError("");
    try {
      await json(
        api,
        base + `/routing/programs/${id}/${action}`,
        action === "approve"
          ? { program_hash: detail.program_hash, approval_ref: reference }
          : {},
      );
      const next = await api(base + `/routing/programs/${id}`);
      if (version === selection.current && project === generation.current) {
        setDetail(next);
        setRows((old) => old.map((row) => (row.id === id ? next : row)));
        setReviewed(false);
      }
    } catch (e) {
      // Reconcile by reading this identity. An approval POST is never automatically retried.
      if (version === selection.current && project === generation.current)
        setError(
          explain(e) + " Refresh this program before taking another action.",
        );
    } finally {
      if (project === generation.current) setBusy(false);
    }
  }

  return (
    <section aria-label="Automatic routing programs">
      <h3>One budget, from simple alternatives to confirmation</h3>
      <p>
        Try cheaper native models first. If none passes, use the reserved
        training and fitting path, then validate a router. Confirm one frozen
        choice on untouched cases. Failed trials remain in the total.
      </p>
      <p className="hint">
        A worker must be running. This evaluates proposed changes; it does not
        change application traffic. Fitting and router hosting dollars remain
        unknown. Synthetic results cannot establish real model quality.
      </p>
      {error && <p role="alert">{error}</p>}
      {admin && (
        <details>
          <summary>Prepare a routing program</summary>
          {!available.length && (
            <p>
              Prepare a source-bound training template below, or with{" "}
              <code>cutback workflow prepare --learned-routing</code>. Leave
              that template unapproved; approve the whole program here instead.
            </p>
          )}
          <label className="field">
            Baseline cost environment
            <select
              value={environment}
              onChange={(e) => setEnvironment(e.target.value)}
            >
              {environments.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.name}
                </option>
              ))}
            </select>
          </label>
          <Form
            title="Freeze routing program"
            disabled={busy || !available.length || !snapshots.length}
            submit="Prepare whole routing budget"
            onSubmit={async (data) => {
              const version = generation.current;
              const body = {
                template_plan_id: data.template_plan_id,
                baseline_snapshot_id: data.snapshot_id,
                budget: {
                  max_usd: String(data.max_usd),
                  max_requests: Number(data.max_requests),
                  max_wall_seconds: Number(data.max_wall_seconds),
                },
                conservative_savings_per_success_usd: data.savings
                  ? String(data.savings)
                  : null,
                forecast_successful_tasks: data.volume
                  ? Number(data.volume)
                  : null,
              };
              const key = JSON.stringify(body);
              if (prepared.current?.key !== key)
                prepared.current = { key, id: crypto.randomUUID() };
              const value = await json(api, base + "/routing/programs", {
                ...body,
                request_id: prepared.current.id,
              });
              if (version !== generation.current) return;
              setRows((old) => [
                value,
                ...old.filter((row) => row.id !== value.id),
              ]);
              await select(value.id);
            }}
          >
            <Select
              label="Source-bound training template"
              name="template_plan_id"
              required
            >
              {available.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.specification.workflow} — {row.id}
                </option>
              ))}
            </Select>
            <Select
              label="Observed baseline task-cost snapshot"
              name="snapshot_id"
              required
            >
              {snapshots.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.workflow} — {row.id}
                </option>
              ))}
            </Select>
            <Field
              label="Whole program maximum USD"
              name="max_usd"
              type="number"
              min="0.000001"
              max="10000"
              step="0.000001"
              required
            />
            <Field
              label="Whole program maximum requests"
              name="max_requests"
              type="number"
              min="1"
              max="10000"
              required
            />
            <Field
              label="Whole program maximum seconds"
              name="max_wall_seconds"
              type="number"
              min="5"
              max="3600"
              required
            />
            <Field
              label="Assumed savings per successful task, USD"
              name="savings"
              type="number"
              min="0"
              step="0.000001"
            />
            <Field
              label="Assumed future successful tasks"
              name="volume"
              type="number"
              min="1"
              max="1000000000"
            />
            <p className="hint">
              These are your opportunity assumptions. Unknown assumptions cause
              abstention. The compiler may reduce the number of candidates, but
              never reduces confirmation cases to fit a budget.
            </p>
          </Form>
          {snapshotCursor && (
            <button disabled={busy} onClick={() => more("snapshots")}>
              Load more baseline snapshots
            </button>
          )}
        </details>
      )}
      <h4>Programs</h4>
      {!rows.length && <p>No routing programs in this project yet.</p>}
      <ul>
        {rows.map((row) => (
          <li key={row.id}>
            <button disabled={busy} onClick={() => select(row.id)}>
              Inspect routing program {row.id}
            </button>{" "}
            — {labels[row.status] || row.status}
          </li>
        ))}
      </ul>
      {cursor && (
        <button disabled={busy} onClick={() => more("programs")}>
          Load more routing programs
        </button>
      )}
      {detail && (
        <section aria-label="Selected routing program">
          <h4>Routing program {detail.id}</h4>
          <p role="status">{labels[detail.status] || detail.status}</p>
          <button disabled={busy} onClick={() => select(detail.id)}>
            Refresh routing program
          </button>
          <dl>
            <dt>Native requests reserved</dt>
            <dd>
              {detail.requests} /{" "}
              {String(detail.economic_envelope.max_requests || "Not eligible")}
            </dd>
            <dt>Reserved native cost</dt>
            <dd>{money(detail.reserved_usd)}</dd>
            <dt>Known native spend subtotal</dt>
            <dd>
              {money(detail.known_spend_subtotal_usd)} —{" "}
              {detail.billing_complete
                ? "all requests have known cost"
                : "billing incomplete"}
            </dd>
            <dt>Fit compute and router hosting cost</dt>
            <dd>Unknown</dd>
            <dt>Deadline</dt>
            <dd>{detail.deadline_at || "Starts at approval"}</dd>
          </dl>
          {typeof detail.summary.decision === "string" && (
            <p>
              Decision: {String(detail.summary.decision).replaceAll("_", " ")}
            </p>
          )}
          {typeof detail.summary.code === "string" && (
            <p>Result: {String(detail.summary.code).replaceAll("_", " ")}</p>
          )}
          <ol>
            {detail.runs.map((row: Data) => (
              <li key={row.id}>
                {String(row.phase).replaceAll("_", " ")}: {row.status};{" "}
                {row.requests} native requests; known subtotal{" "}
                {money(row.known_spend_subtotal_usd)}
              </li>
            ))}
          </ol>
          <details>
            <summary>Frozen native effort and opportunity bounds</summary>
            <pre tabIndex={0} aria-label="Frozen routing budget details">
              {JSON.stringify(detail.economic_envelope, null, 2)}
            </pre>
          </details>
          {admin && (
            <>
              <Field
                label="Routing program content purpose"
                name="routing_program_purpose"
                value={purpose}
                onChange={(e) => setPurpose(e.target.value)}
              />
              <button disabled={busy || !purpose} onClick={readPrivate}>
                Read private routing program
              </button>
              {privateContent && (
                <details open>
                  <summary>
                    Exact private routing instructions and requirements
                  </summary>
                  <pre tabIndex={0} aria-label="Private routing specification">
                    {JSON.stringify(privateContent.specification, null, 2)}
                  </pre>
                </details>
              )}
              {detail.status === "READY_FOR_APPROVAL" && (
                <>
                  <p>
                    Exact program hash: <code>{detail.program_hash}</code>
                  </p>
                  <Field
                    label="Routing program approval reference"
                    name="routing_program_approval_reference"
                    value={reference}
                    onChange={(e) => setReference(e.target.value)}
                  />
                  <label>
                    <input
                      type="checkbox"
                      checked={reviewed}
                      onChange={(e) => setReviewed(e.target.checked)}
                    />{" "}
                    I reviewed the exact private instructions, quality
                    requirements and whole program budget.
                  </label>
                  <button
                    disabled={
                      busy ||
                      !reference ||
                      !reviewed ||
                      privateContent?.program_hash !== detail.program_hash
                    }
                    onClick={() => act("approve")}
                  >
                    Approve whole routing program
                  </button>
                </>
              )}
              {active.has(detail.status) && (
                <button disabled={busy} onClick={() => act("cancel")}>
                  Cancel routing program
                </button>
              )}
              {detail.status === "COMPLETED" &&
                detail.summary.code === "confirmation_completed" && (
                  <p>
                    Ask your application agent to run{" "}
                    <code>
                      cutback routing program proposal --purpose-reference
                      PURPOSE --output PRIVATE.json
                    </code>{" "}
                    from the saved job, then verify the exact diff and
                    application checks before review.
                  </p>
                )}
            </>
          )}
        </section>
      )}
    </section>
  );
}
