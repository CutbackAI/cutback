import { useCallback, useEffect, useRef, useState } from "react";
import type { components } from "./api.generated";
import { CacheWorkloadCheck } from "./cache-workload";
import {
  type API,
  type Data,
  Field,
  Select,
  explain,
  json,
  values,
} from "./ui";

type AnalysisInput = components["schemas"]["NativeProfileAnalysisInput"];
const count = (value: unknown) =>
  typeof value === "number" ? String(value) : "Unknown";

export function RequestProfiles({
  api,
  project,
  canApprove,
}: {
  api: API;
  project: string;
  canApprove: boolean;
}) {
  const base = `/projects/${project}/native-request-profiles/analyses`;
  const [rows, setRows] = useState<Data[]>([]);
  const [policies, setPolicies] = useState<Data[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [offset, setOffset] = useState(0);
  const generation = useRef(0);
  const pending = useRef<AnalysisInput | null>(null);
  const load = useCallback(async () => {
    const serial = ++generation.current;
    if (!canApprove) return;
    const [receipts, available] = await Promise.all([
      api(`${base}?limit=25&offset=${offset}`),
      api(`/projects/${project}/capture-policies`),
    ]);
    if (serial !== generation.current) return;
    setRows(receipts);
    setPolicies(
      available.filter(
        (row: Data) => row.enabled && row.policy?.native_request_profile,
      ),
    );
  }, [api, base, project, offset, canApprove]);
  useEffect(() => {
    load().catch((error) => setMessage(explain(error)));
    return () => {
      generation.current++;
    };
  }, [load]);
  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    setMessage("");
    try {
      await action();
      await load();
    } catch (error) {
      setMessage(explain(error));
    } finally {
      setBusy(false);
    }
  }
  if (!canApprove)
    return (
      <section>
        <h2>Request reuse</h2>
        <p>
          Project administrator access is required to authorize encrypted
          request analysis.
        </p>
      </section>
    );
  return (
    <section aria-label="Request reuse analysis">
      <h2>Could repeated requests benefit from caching?</h2>
      <p>
        Analyze a retained window of explicitly authorized SDK profiles.
        Ordinary metadata never contains request fingerprints. This models
        possible reuse; it does not measure cache delivery, semantic quality or
        production savings.
      </p>
      <p>
        No inference calls are made. Encrypted storage reads may incur
        infrastructure charges. Each job starts once, reads at most 1,000
        profiles, and expires within one hour.
      </p>
      <form
        aria-label="New request reuse analysis"
        onSubmit={(event) => {
          event.preventDefault();
          const data = values(event.currentTarget);
          const body = pending.current ?? {
            request_id: crypto.randomUUID(),
            policy_id: String(data.policy),
            environment: String(data.environment),
            workflow: String(data.workflow),
            purpose: "dashboard-cache-assessment",
            window_start: String(data.start),
            window_end: String(data.end),
            max_observations: 1000,
            max_wall_seconds: 60,
          };
          pending.current = body;
          act(async () => {
            await json(api, base, body);
            pending.current = null;
          });
        }}
      >
        <Select label="Approved request-profile policy" name="policy" required>
          <option value="">Choose an explicitly approved policy</option>
          {policies.map((row) => (
            <option key={row.id} value={row.id}>
              {row.id}
            </option>
          ))}
        </Select>
        <Field
          label="Profile environment"
          name="environment"
          defaultValue="staging"
          required
        />
        <Field label="Profile workflow" name="workflow" required />
        <Field
          label="Window start (ISO with UTC offset)"
          name="start"
          required
          placeholder="2026-01-01T12:00:00Z"
        />
        <Field
          label="Window end (ISO with UTC offset)"
          name="end"
          required
          placeholder="2026-01-01T13:00:00Z"
        />
        <button disabled={busy || !policies.length}>
          Queue bounded analysis
        </button>
        <p className="hint">
          An uncertain submission keeps its original request ID. Submit again to
          reconcile that same job before preparing another window.
        </p>
      </form>
      {message && <p role="alert">{message}</p>}
      <button disabled={busy} onClick={() => act(load)}>
        Refresh analyses
      </button>
      {!rows.length && (
        <p>
          No retained analyses. Enable approved SDK capture first, then choose a
          completed window.
        </p>
      )}
      {rows.map((row) => (
        <article key={row.id} aria-label={`Analysis ${row.id}`}>
          <h3>
            {row.workflow} · {row.environment}
          </h3>
          <p>
            {row.state} · {row.window_start} to {row.window_end}
          </p>
          <p>
            Retained until {row.expires_at}. Storage attempts:{" "}
            {count(row.attempts)}.
          </p>
          {row.failure_code && (
            <p role="status">
              Analysis stopped. Inspect current authority, encrypted content and
              worker health. Started uncertain reads are not replayed.
            </p>
          )}
          {row.summary && (
            <>
              <p>
                <strong>
                  {row.summary.cache_trial_eligible
                    ? "Repeat evidence supports a separate cache trial"
                    : "Insufficient evidence for automatic cache selection"}
                </strong>
              </p>
              <dl>
                <dt>Retained metadata attempts</dt>
                <dd>{count(row.summary.metadata_attempts)}</dd>
                <dt>Profiled attempts</dt>
                <dd>{count(row.summary.profiled_attempts)}</dd>
                <dt>Modeled potential hits</dt>
                <dd>{count(row.summary.modeled_potential_hits)}</dd>
                <dt>Overlapping cold misses</dt>
                <dd>{count(row.summary.overlapping_cold_misses)}</dd>
                <dt>Prospective TTL</dt>
                <dd>{count(row.summary.prospective_ttl_seconds)} seconds</dd>
                <dt>Modeled avoided provider cost</dt>
                <dd>
                  {row.summary.modeled_avoided_provider_cost_usd ?? "Unknown"}{" "}
                  USD
                </dd>
                <dt>
                  Infrastructure cost, semantic quality and realized savings
                </dt>
                <dd>Unknown</dd>
              </dl>
              {!!row.summary.abstention_reasons?.length && (
                <p>
                  Evidence gaps: {row.summary.abstention_reasons.join(", ")}
                </p>
              )}
              <p className="hint">
                The model starts cold, fills only after native completion, and
                assumes zero cache transport latency. Sampled or missing
                profiles cannot establish a traffic census.
              </p>
              {row.state === "COMPLETED" && (
                <CacheWorkloadCheck
                  api={api}
                  project={project}
                  analysis={row.id}
                  environment={row.environment}
                  workflow={row.workflow}
                />
              )}
            </>
          )}
          {["QUEUED", "RUNNING"].includes(row.state) && (
            <button
              disabled={busy}
              onClick={() =>
                act(() => json(api, `${base}/${row.id}/cancel`, {}))
              }
            >
              Cancel analysis {row.id.slice(0, 8)}
            </button>
          )}
        </article>
      ))}
      <button
        disabled={busy || offset === 0}
        onClick={() => setOffset(Math.max(0, offset - 25))}
      >
        Previous analyses
      </button>
      <button
        disabled={busy || rows.length < 25 || offset >= 100000}
        onClick={() => setOffset(offset + 25)}
      >
        Next analyses
      </button>
    </section>
  );
}
