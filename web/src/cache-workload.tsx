import { useEffect, useRef, useState } from "react";
import type { components } from "./api.generated";
import { type API, explain, json } from "./ui";

type Input = components["schemas"]["CacheWorkloadInput"];
type Receipt = components["schemas"]["CacheWorkloadAssessment"];

export function CacheWorkloadCheck({
  api,
  project,
  analysis,
  environment,
  workflow,
}: {
  api: API;
  project: string;
  analysis: string;
  environment: string;
  workflow: string;
}) {
  const [input, setInput] = useState<Input | null>(null);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  useEffect(
    () => () => {
      generation.current++;
    },
    [project, analysis],
  );
  return (
    <details>
      <summary>Check this window against your application</summary>
      <p>
        Export source metadata from your application with{" "}
        <code>cutback workflow workload-input --help</code>. The file contains
        source hashes, a commit and declared cache costs. It contains no request
        payloads. Keep it private. The agent can perform this check directly
        with <code>cutback workflow assess-workload</code>.
      </p>
      <label className="field">
        Source workload input (private metadata JSON)
        <input
          type="file"
          accept="application/json,.json"
          disabled={busy}
          onChange={async (event) => {
            const serial = ++generation.current;
            const file = event.target.files?.[0];
            event.target.value = "";
            setInput(null);
            setReceipt(null);
            setMessage("");
            if (!file) return;
            try {
              if (file.size > 262144)
                throw new Error("BOUNDED_WORKLOAD_METADATA_REQUIRED");
              const parsed = JSON.parse(await file.text());
              if (serial !== generation.current) return;
              if (
                !parsed ||
                typeof parsed !== "object" ||
                Array.isArray(parsed) ||
                Object.keys(parsed).sort().join(",") !==
                  "baseline_model,cache_execution,source_binding" ||
                parsed.source_binding?.workflow_id !== workflow ||
                !parsed.cache_execution ||
                typeof parsed.baseline_model !== "string"
              )
                throw new Error("MATCHING_WORKLOAD_METADATA_REQUIRED");
              setInput(parsed);
            } catch {
              if (serial === generation.current)
                setMessage(
                  "Use a matching workload-input export smaller than 256 KiB. File contents omitted.",
                );
            }
          }}
        />
      </label>
      <button
        disabled={busy || !input}
        onClick={async () => {
          const serial = ++generation.current;
          setBusy(true);
          setReceipt(null);
          setMessage("");
          try {
            const next: Receipt = await json(
              api,
              `/projects/${project}/native-request-profiles/analyses/${analysis}/cache-trial`,
              input,
            );
            if (serial !== generation.current) return;
            if (
              next.project_id !== project ||
              next.analysis_id !== analysis ||
              next.environment !== environment ||
              next.workflow !== workflow ||
              next.version !== "cache-workload-assessment-v1" ||
              next.paid_calls_authorized !== false ||
              next.source_change_authorized !== false ||
              next.provider_calls !== 0 ||
              next.encrypted_storage_reads !== 0
            )
              throw new Error("WORKLOAD_RESPONSE_SCOPE_MISMATCH");
            setReceipt(next);
          } catch (error) {
            if (serial === generation.current) setMessage(explain(error));
          } finally {
            if (serial === generation.current) setBusy(false);
          }
        }}
      >
        Assess source-matched trial eligibility
      </button>
      <p className="hint">
        This check reads retained metadata only. It starts no cache, provider
        request or source change.
      </p>
      {message && <p role="alert">{message}</p>}
      {receipt && (
        <div role="status" aria-label="Source workload assessment">
          <p>
            <strong>
              {receipt.decision === "prepare_separate_cache_trial"
                ? "This source window supports preparing a separate cache trial"
                : "This source window does not qualify a cache trial"}
            </strong>
          </p>
          {!!receipt.reasons.length && (
            <p>Evidence gaps: {receipt.reasons.join(", ")}</p>
          )}
          <dl>
            <dt>Known provider cost in this window</dt>
            <dd>{receipt.provider_modeled_window_cost_usd ?? "Unknown"} USD</dd>
            <dt>Declared cache operation upper cost</dt>
            <dd>
              {receipt.declared_cache_operation_upper_cost_usd ?? "Unknown"} USD
            </dd>
            <dt>Modeled net native-window opportunity</dt>
            <dd>
              {receipt.modeled_net_native_window_opportunity_usd ?? "Unknown"}{" "}
              USD
            </dd>
            <dt>Whole-task cost, production hit rate and realized savings</dt>
            <dd>Unknown</dd>
          </dl>
          <p>
            Independent quality and cache-delivery checks are still required.
            Source lineage is client reported. This assessment grants no paid
            budget or source-change approval. Valid until {receipt.valid_until}.
          </p>
          {receipt.decision === "prepare_separate_cache_trial" && (
            <p>
              In a matching source installation, use{" "}
              <code>
                cutback workflow prepare --automatic --exact-cache-contract
                PRIVATE_CONTRACT --help
              </code>{" "}
              to prepare a workload-bound native/model/cache comparison with
              permitted labeled examples. The collector rechecks this exact
              window before approval and every execution call. Preparation
              starts no inference.
            </p>
          )}
        </div>
      )}
    </details>
  );
}
