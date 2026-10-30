import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { components } from "./api.generated";
import { type API, Field, Select, explain, json } from "./ui";
import { gatewayModeledUSD } from "./gateway-money";

type Receipt = components["schemas"]["ProviderBatchReceipt"];
type Plan = {
  id: string;
  approval_status: string;
  specification: Record<string, any>;
};

export function ProviderBatches({
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
  const base = `/projects/${project}/provider-batches`;
  const [rows, setRows] = useState<Receipt[]>([]);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [plan, setPlan] = useState("");
  const [storage, setStorage] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [offset, setOffset] = useState(0);
  const context = useMemo(
    () => ({ api, project, offset }),
    [api, project, offset],
  );
  const latest = useRef(context);
  const sequence = useRef(0);
  latest.current = context;
  const load = useCallback(async () => {
    const request = ++sequence.current;
    async function availablePlans(): Promise<Plan[]> {
      const result: Plan[] = [];
      for (let start = 0; start < 1000; start += 100) {
        const page: Plan[] = await api(
          `/projects/${project}/experiments/plans?limit=100&offset=${start}`,
        );
        result.push(...page);
        if (page.length < 100) break;
      }
      return result;
    }
    const [batches, available] = await Promise.all([
      api(`${base}?limit=25&offset=${offset}`),
      availablePlans(),
    ]);
    if (latest.current !== context || sequence.current !== request) return;
    setRows(batches);
    setPlans(
      available.filter(
        (value: Plan) =>
          value.approval_status === "pending" &&
          value.specification?.split === "validation" &&
          value.specification?.baseline?.provider === "openai",
      ),
    );
  }, [api, base, project, offset, context]);
  useEffect(() => {
    let alive = true;
    setRows([]);
    setPlans([]);
    setPlan("");
    setStorage(false);
    setBusy(false);
    setMessage("");
    load().catch((error) => {
      if (alive) setMessage(explain(error));
    });
    return () => {
      alive = false;
      sequence.current += 1;
    };
  }, [load]);
  async function act(action: () => Promise<unknown>) {
    const owner = context;
    setBusy(true);
    setMessage("");
    try {
      await action();
      if (latest.current !== owner) return;
      await load();
      if (latest.current !== owner) return;
      setMessage("Saved. The worker performs provider operations separately.");
    } catch (error) {
      if (latest.current === owner) setMessage(explain(error));
    } finally {
      if (latest.current === owner) setBusy(false);
    }
  }
  return (
    <section aria-label="Provider batch processing">
      <h2>Provider batches</h2>
      <p>
        Run an approved validation dataset asynchronously. Each provider batch
        can take 24 hours; turnaround is measured separately from request
        latency. Completed requests can be charged even after cancellation.
      </p>
      <p>
        Batch uploads retain data at the provider and are incompatible with zero
        data retention. Local examples and plans remain in your authorized
        encrypted store. Unknown usage keeps its charge reservation; interrupted
        submissions are never repeated automatically.
      </p>
      <button type="button" disabled={busy} onClick={() => act(load)}>
        Refresh batches
      </button>
      {canWrite && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            act(() =>
              json(api, base, {
                plan_id: plan,
                provider_storage_approved: storage,
                max_turnaround_seconds: 90000,
              }),
            );
          }}
        >
          <Select
            name="batch-plan"
            label="Pending validation plan"
            value={plan}
            required
            onChange={(event) => setPlan(event.target.value)}
          >
            <option value="">Choose an existing plan</option>
            {plans.map((value) => (
              <option key={value.id} value={value.id}>
                {value.specification.workflow || "Validation"} ·{" "}
                {value.id.slice(0, 8)}
              </option>
            ))}
          </Select>
          <label>
            <input
              type="checkbox"
              required
              checked={storage}
              onChange={(event) => setStorage(event.target.checked)}
            />
            I authorize provider-side storage of this plan’s inputs and results
            for batch processing, with a requested 48-hour file expiry and
            explicit cleanup.
          </label>
          <p>
            Preparation does not authorize spending. Review the frozen plan and
            storage contract before approving its one execution.
          </p>
          <button type="submit" disabled={busy || !plan || !storage}>
            Prepare batch approval
          </button>
        </form>
      )}
      {message && <p role="status">{message}</p>}
      <div className="table-scroll">
        <table>
          <caption>Asynchronous provider jobs and billing coverage</caption>
          <thead>
            <tr>
              <th>Batch</th>
              <th>State</th>
              <th>Reserved items</th>
              <th>Billing</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{row.id.slice(0, 8)}</td>
                <td>{row.state}</td>
                <td>{row.canonical_call_reservations}</td>
                <td>
                  {row.report.billing_complete === true
                    ? "Complete native usage"
                    : "Unknown or pending"}
                </td>
                <td>
                  <details>
                    <summary>Review batch {row.id.slice(0, 8)}</summary>
                    <BatchReview
                      row={row}
                      api={api}
                      base={base}
                      busy={busy}
                      canApprove={canApprove}
                      action={act}
                    />
                  </details>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!rows.length && (
        <p>
          No prepared batches on this page. Start with a plain text validation
          plan in Experiments. Test holdouts, source changes, tools and judges
          require their separate approved workflows.
        </p>
      )}
      <nav aria-label="Batch pages">
        <button
          disabled={busy || offset === 0}
          onClick={() => setOffset(Math.max(0, offset - 25))}
        >
          Previous batches
        </button>
        <button
          disabled={busy || rows.length < 25}
          onClick={() => setOffset(offset + 25)}
        >
          Next batches
        </button>
      </nav>
    </section>
  );
}

function BatchReview({
  row,
  api,
  base,
  busy,
  canApprove,
  action,
}: {
  row: Receipt;
  api: API;
  base: string;
  busy: boolean;
  canApprove: boolean;
  action: (operation: () => Promise<unknown>) => Promise<void>;
}) {
  const [approval, setApproval] = useState("");
  const [recoveryModel, setRecoveryModel] = useState("");
  const [recoveryKind, setRecoveryKind] = useState("file_id");
  const [recoveryID, setRecoveryID] = useState("");
  const specification = row.specification.execution_plan as
    Record<string, any> | undefined;
  return (
    <section aria-label={`Frozen batch ${row.id.slice(0, 8)}`}>
      <p>
        Approval hash: <code>{row.approval_hash}</code>
      </p>
      <p>
        Plan: <code>{row.plan_id}</code>
      </p>
      <p>
        Budget: {gatewayModeledUSD(specification?.budget?.max_usd)} ·{" "}
        {specification?.budget?.max_requests ?? "Unknown"} maximum native
        requests. Every item reserves its full online upper bound before
        submission; final modeled cost uses the frozen batch price contract.
      </p>
      <p>
        Provider storage is authorized. Completion window: 24 hours. Operator
        turnaround limit:{" "}
        {Number(row.specification.max_turnaround_seconds) / 3600} hours.
        Per-request latency: unknown. This validation cannot approve a source
        patch.
      </p>
      <p>
        Active processing allowance:{" "}
        {specification?.budget?.max_wall_seconds ?? "Unknown"} seconds.
        Accounted: {row.accounted_active_processing_us / 1000000} seconds,
        including {row.uncertain_active_processing_us / 1000000} seconds held
        after uncertain work. Currently reserved:{" "}
        {row.held_active_processing_us / 1000000} seconds. Waiting for the
        provider is excluded.
      </p>
      <ul>
        {[specification?.baseline, ...(specification?.candidates || [])]
          .filter(Boolean)
          .map((model) => (
            <li key={model.key}>
              {model.key}: {model.provider} / {model.model}, input ceiling{" "}
              {model.max_input_tokens}, output ceiling {model.max_output_tokens}
            </li>
          ))}
      </ul>
      {row.state === "PENDING" && canApprove && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            action(() =>
              json(api, `${base}/${row.id}/approve`, {
                approval_hash: row.approval_hash,
                approval_ref: approval,
              }),
            );
          }}
        >
          <Field
            name="batch-approval-reference"
            label="Batch approval reference"
            required
            value={approval}
            maxLength={200}
            pattern={"[a-zA-Z0-9_.:\\/\\-]{1,200}"}
            onChange={(event) => setApproval(event.target.value)}
          />
          <button type="submit" disabled={busy || !approval}>
            Approve this batch budget and storage
          </button>
        </form>
      )}
      {canApprove &&
        !["COMPLETED", "CANCELLED", "FAILED", "CLEANUP_PENDING"].includes(
          row.state,
        ) && (
          <button
            disabled={busy}
            onClick={() =>
              action(() => json(api, `${base}/${row.id}/cancel`, {}))
            }
          >
            Request cancellation
          </button>
        )}
      {canApprove && ["UNCERTAIN", "CANCEL_REQUESTED"].includes(row.state) && (
        <div>
          <button
            disabled={busy}
            onClick={() =>
              action(() => json(api, `${base}/${row.id}/reconcile`, {}))
            }
          >
            Find submitted jobs without resubmitting
          </button>
          <details>
            <summary>Recover using a known provider identifier</summary>
            <p>
              Use an existing identifier after a bounded lookup cannot find the
              original operation. The worker verifies its frozen identity before
              use. This never creates a replacement job.
            </p>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                action(() =>
                  json(api, `${base}/${row.id}/reconcile`, {
                    hints: [
                      { model_key: recoveryModel, [recoveryKind]: recoveryID },
                    ],
                  }),
                );
              }}
            >
              <Select
                label="Recovery model"
                name="recovery-model"
                required
                value={recoveryModel}
                onChange={(event) => setRecoveryModel(event.target.value)}
              >
                <option value="">Select the interrupted model</option>
                {row.provider_batches.map((part) => (
                  <option
                    key={String(part.model_key)}
                    value={String(part.model_key)}
                  >
                    {String(part.model_key)} · {String(part.state)}
                  </option>
                ))}
              </Select>
              <Select
                label="Provider identifier kind"
                name="recovery-kind"
                value={recoveryKind}
                onChange={(event) => {
                  setRecoveryKind(event.target.value);
                  setRecoveryID("");
                }}
              >
                <option value="file_id">Input file</option>
                <option value="batch_id">Batch job</option>
              </Select>
              <Field
                label="Existing provider identifier"
                name="recovery-id"
                required
                maxLength={186}
                value={recoveryID}
                onChange={(event) => setRecoveryID(event.target.value)}
                pattern={
                  recoveryKind === "file_id"
                    ? "file[-_][A-Za-z0-9_-]{1,180}"
                    : "batch_[A-Za-z0-9_-]{1,180}"
                }
              />
              <button disabled={busy || !recoveryModel || !recoveryID}>
                Verify existing operation
              </button>
            </form>
          </details>
        </div>
      )}
      {typeof row.report.code === "string" && (
        <p>{explain(new Error(row.report.code))}</p>
      )}
      {row.report.models !== undefined && row.report.models !== null && (
        <details>
          <summary>Validation measurements and known costs</summary>
          <BatchMeasurements value={row.report.models} />
        </details>
      )}
      <p>
        Vendor file cleanup:{" "}
        {row.provider_batches.length
          ? row.provider_batches
              .map((part) => String(part.cleanup || "pending"))
              .join(", ")
          : "No files uploaded"}
      </p>
    </section>
  );
}

export function ProviderBatchEvidence({
  report,
}: {
  report: Receipt["report"];
}) {
  return (
    <section aria-label="Native batch experiment evidence">
      <h3>Native batch validation</h3>
      <p>
        {report.billing_complete === true
          ? "Complete modeled native charges."
          : "Native cost coverage is unknown or pending."}{" "}
        Provider turnaround is separate from per-request latency, which remains
        unknown. This validation does not authorize an application change.
      </p>
      {typeof report.code === "string" && (
        <p>{explain(new Error(report.code))}</p>
      )}
      {report.models !== undefined && report.models !== null && (
        <BatchMeasurements value={report.models} />
      )}
      <p>
        Use Provider batches to inspect the frozen storage/budget approval,
        cancellation and vendor-file cleanup.
      </p>
    </section>
  );
}

function BatchMeasurements({ value }: { value: unknown }) {
  const rows = Array.isArray(value) ? value : [];
  const score = (number: unknown) =>
    typeof number === "number" && Number.isFinite(number)
      ? number.toFixed(3)
      : "Unknown";
  return (
    <>
      <p>
        These are descriptive validation measurements. Missing answers count as
        failures. Independent test evidence and per-request latency remain
        unavailable.
      </p>
      <div className="table-scroll">
        <table>
          <caption>Native batch validation measurements</caption>
          <thead>
            <tr>
              <th>Model</th>
              <th>Repeat</th>
              <th>Score</th>
              <th>Frozen groups</th>
              <th>Known charges</th>
              <th>Modeled cost</th>
            </tr>
          </thead>
          <tbody>
            {rows.flatMap((row: any) =>
              (Array.isArray(row.runs) ? row.runs : []).map((run: any) => (
                <tr key={`${row.model_key}-${run.repeat}`}>
                  <td>{row.model_key}</td>
                  <td>{run.repeat + 1}</td>
                  <td>{score(run.metric?.value)}</td>
                  <td>{run.metric?.independent_group_n ?? "Unknown"}</td>
                  <td>
                    {row.known_cost_requests} / {row.requests}
                  </td>
                  <td>{gatewayModeledUSD(row.modeled_cost_usd)}</td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      </div>
      {rows.flatMap((row: any) =>
        (Array.isArray(row.runs) ? row.runs : []).flatMap((run: any) =>
          (Array.isArray(run.criteria) ? run.criteria : []).map(
            (criterion: any) => (
              <p key={`${row.model_key}-${run.repeat}-${criterion.key}`}>
                {row.model_key} · repeat {run.repeat + 1} · {criterion.key}:{" "}
                {score(criterion.metric?.value)} / required minimum{" "}
                {score(criterion.minimum)}
                {criterion.required ? " (required)" : ""}
              </p>
            ),
          ),
        ),
      )}
    </>
  );
}
